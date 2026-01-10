import os
import datetime
import pandas as pd
import gspread
import yfinance as yf
import FinanceDataReader as fdr
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
import requests
from bs4 import BeautifulSoup
import json
import time

# --- 설정 및 상수 ---
APP_NAME = "DailyStockUpdater"
VERSION = "v1.2.0_20260110"

# 환경 변수 및 설정
CREDENTIALS_JSON = os.getenv('GOOGLE_CREDENTIALS_JSON')
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID')
CALENDAR_ID = os.getenv('CALENDAR_ID', 'primary')

class StockDataUpdater:
    def __init__(self):
        self.creds = self._load_credentials()
        self.gc = gspread.authorize(self.creds)
        self.sh = self.gc.open_by_key(SPREADSHEET_ID)
        self.calendar_service = build('calendar', 'v3', credentials=self.creds)
        self.kr_name_map = self._get_kr_name_map()

    def _load_credentials(self):
        if CREDENTIALS_JSON:
            info = json.loads(CREDENTIALS_JSON)
            return Credentials.from_service_account_info(info, scopes=[
                'https://www.googleapis.com/auth/spreadsheets',
                'https://www.googleapis.com/auth/calendar'
            ])
        else:
            return Credentials.from_service_account_file('credentials.json', scopes=[
                'https://www.googleapis.com/auth/spreadsheets',
                'https://www.googleapis.com/auth/calendar'
            ])

    def _get_kr_name_map(self):
        try:
            df = fdr.StockListing('KRX')
            return dict(zip(df['Code'], df['Name']))
        except Exception as e:
            print(f"Error fetching KRX listing: {e}")
            return {}

    def _format_large_number(self, n):
        if n is None or pd.isna(n): return "-"
        if n >= 1e12: return f"{n/1e12:.2f}T"
        if n >= 1e9: return f"{n/1e9:.2f}B"
        if n >= 1e6: return f"{n/1e6:.2f}M"
        if n >= 1e3: return f"{n/1e3:.2f}K"
        return f"{int(n):,}"

    def _format_price(self, n, asset_type):
        if n is None or pd.isna(n): return "-"
        if asset_type in ['KR', 'KRW']: return f"{int(n):,}"
        return f"{n:,.2f}"

    def get_market_indices(self):
        """핵심 시장 지수 및 환율 수집"""
        indices = {
            '^KS11': 'KOSPI', '^KQ11': 'KOSDAQ', 
            '^GSPC': 'S&P500', '^IXIC': 'NASDAQ',
            'USDKRW=X': 'USD/KRW'
        }
        results = {}
        for ticker, name in indices.items():
            try:
                stock = yf.Ticker(ticker)
                hist = stock.history(period="2d")
                if len(hist) < 2: continue
                curr = hist['Close'].iloc[-1]
                prev = hist['Close'].iloc[-2]
                change = curr - prev
                change_rate = (change / prev) * 100
                results[name] = {
                    'price': curr,
                    'change': change,
                    'rate': change_rate
                }
            except Exception as e:
                print(f"Error fetching index {name}: {e}")
        return results

    def get_stock_data(self, tickers, asset_type):
        """주식/코인 상세 데이터 수집 (거래량 분석 포함)"""
        data = []
        for ticker in tickers:
            try:
                sym = ticker
                if asset_type == 'KR':
                    sym = f"{ticker}.KS" if len(ticker) == 6 else ticker
                elif asset_type == 'Coin':
                    sym = f"{ticker}-USD"
                
                stock = yf.Ticker(sym)
                # 20일 평균 거래량 확인을 위해 1개월 데이터 수집
                hist = stock.history(period="1mo")
                if len(hist) < 2: continue
                
                curr = hist.iloc[-1]
                prev = hist.iloc[-2]
                avg_vol = hist['Volume'].iloc[:-1].mean()
                curr_vol = curr['Volume']
                vol_spike = (curr_vol / avg_vol) if avg_vol > 0 else 0
                
                price = curr['Close']
                change_rate = ((price - prev['Close']) / prev['Close']) * 100
                
                name = stock.info.get('longName') or stock.info.get('shortName') or ticker
                if asset_type == 'KR': name = self.kr_name_map.get(ticker, name)

                data.append({
                    'Asset': asset_type,
                    'Ticker': ticker,
                    'Name': name,
                    'Price': price,
                    'FormattedPrice': self._format_price(price, asset_type),
                    'ChangeRate': round(change_rate, 2),
                    'Volume': curr_vol,
                    'VolSpike': round(vol_spike, 2),
                    'MarketCap': stock.info.get('marketCap', 0),
                    'News': self._get_news_top1(stock, ticker, asset_type)
                })
            except Exception as e:
                print(f"Error fetching {asset_type} {ticker}: {e}")
        return data

    def _get_news_top1(self, stock_obj, ticker, asset_type):
        try:
            if asset_type == 'KR':
                url = f"https://finance.naver.com/item/news_news.naver?code={ticker}"
                resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
                soup = BeautifulSoup(resp.text, 'html.parser')
                t = soup.select_one('.title a')
                if t: return f"{t.text.strip()} (https://finance.naver.com{t['href']})"
            else:
                news = stock_obj.news
                if news: return f"{news[0]['title']} ({news[0]['link']})"
        except: pass
        return "-"

    def get_watchlist(self):
        """관심종목 탭에서 리스트 읽기"""
        try:
            ws = self.sh.worksheet('관심종목_관리')
            records = ws.get_all_records()
            return records
        except:
            print("Watchlist sheet not found. Creating one...")
            ws = self.sh.add_worksheet(title='관심종목_관리', rows=100, cols=10)
            ws.append_row(['Ticker', 'Name', 'Asset', 'Memo', 'Alert_Price'])
            return []

    def get_monthly_worksheet(self):
        """월별 탭 관리 및 반환"""
        tab_name = datetime.date.today().strftime('%Y-%m')
        try:
            return self.sh.worksheet(tab_name)
        except gspread.exceptions.WorksheetNotFound:
            ws = self.sh.add_worksheet(title=tab_name, rows=1000, cols=15)
            ws.append_row(['Date', 'Market Summary', 'Indices Info', 'Watchlist Status', 'Unusual Stocks', 'Version'])
            return ws

    def process_and_report(self):
        print("1. 수집 중: 시장 지표...")
        indices = self.get_market_indices()
        
        print("2. 수집 중: 관심종목...")
        watchlist_raw = self.get_watchlist()
        watch_data = []
        for item in watchlist_raw:
            if not item.get('Ticker'): continue
            res = self.get_stock_data([str(item['Ticker'])], item.get('Asset', 'US'))
            if res:
                res[0]['Memo'] = item.get('Memo', '')
                watch_data.append(res[0])

        print("3. 수집 중: 주요 마켓 데이터...")
        # 기존 샘플 리스트 (실제로는 다른 시트에서 관리 가능)
        sample_kr = ['005930', '000660', '035720']
        sample_us = ['AAPL', 'TSLA', 'NVDA']
        market_all = self.get_stock_data(sample_kr, 'KR') + self.get_stock_data(sample_us, 'US')

        # 분석: 특이종목 (변동성 15% 이상 OR 거래량 3배 이상)
        unusual = [d for d in market_all if abs(d['ChangeRate']) >= 15.0 or d['VolSpike'] >= 3.0]

        # 요약 텍스트 생성
        idx_summary = " / ".join([f"{k}: {v['price']:,.1f}({v['rate']:+.2f}%)" for k, v in indices.items()])
        
        watch_summary = "\n".join([f"- {d['Name']} ({d['FormattedPrice']} / {d['ChangeRate']:+.2f}%): {d['Memo']}" for d in watch_data])
        unusual_summary = "\n".join([f"- {d['Name']} ({d['FormattedPrice']} / {d['ChangeRate']:+.2f}% / 거래량 {d['VolSpike']}배): {d['News']}" for d in unusual])

        # 시트 기록
        print("4. 기록 중: 월별 일지...")
        ws_monthly = self.get_monthly_worksheet()
        ws_monthly.append_row([
            datetime.date.today().isoformat(),
            f"KR:{indices.get('KOSPI', {}).get('rate', 0):+.2f}%, US:{indices.get('S&P500', {}).get('rate', 0):+.2f}%",
            idx_summary,
            watch_summary if watch_summary else "N/A",
            unusual_summary if unusual_summary else "N/A",
            f"{APP_NAME} {VERSION}"
        ])

        # 캘린더 생성
        print("5. 연동 중: 구글 캘린더...")
        cal_title = f"[투자일지] KOSPI {indices.get('KOSPI',{}).get('rate',0):+.2f}% / S&P500 {indices.get('S&P500',{}).get('rate',0):+.2f}%"
        cal_desc = f"""## ⭐ 관심종목 브리핑
{watch_summary if watch_summary else "등록된 관심종목이 없습니다."}

## 📈 핵심 시장 지표
- 국장: KOSPI {indices.get('KOSPI',{}).get('price',0):,.1f} ({indices.get('KOSPI',{}).get('rate',0):+.2f}%) / KOSDAQ {indices.get('KOSDAQ',{}).get('price',0):,.1f} ({indices.get('KOSDAQ',{}).get('rate',0):+.2f}%)
- 미장: S&P500 {indices.get('S&P500',{}).get('price',0):,.1f} ({indices.get('S&P500',{}).get('rate',0):+.2f}%) / NASDAQ {indices.get('NASDAQ',{}).get('price',0):,.1f} ({indices.get('NASDAQ',{}).get('rate',0):+.2f}%)
- 환율: USD/KRW {indices.get('USD/KRW',{}).get('price',0):,.1f} (전일대비 {indices.get('USD/KRW',{}).get('change',0):+.1f}원)

## 🔥 실시간 특이종목 (거래량/변동성)
{unusual_summary if unusual_summary else "오늘의 특이종목이 없습니다."}

## 🔗 상세 내용 보기
[구글 시트 바로가기](https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID})
"""
        self.create_calendar_event(cal_title, cal_desc)
        print("모든 작업이 완료되었습니다.")

    def create_calendar_event(self, title, description):
        event = {
            'summary': title,
            'description': description,
            'start': {'date': datetime.date.today().isoformat(), 'timeZone': 'Asia/Seoul'},
            'end': {'date': datetime.date.today().isoformat(), 'timeZone': 'Asia/Seoul'},
        }
        try:
            self.calendar_service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
        except Exception as e:
            print(f"Error creating calendar event: {e}")

if __name__ == "__main__":
    if not SPREADSHEET_ID:
        print("ERROR: SPREADSHEET_ID is missing.")
        exit(1)
    
    updater = StockDataUpdater()
    updater.process_and_report()
