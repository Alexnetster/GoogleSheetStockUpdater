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
# GitHub Actions에서는 Secrets로 관리할 예정
CREDENTIALS_JSON = os.getenv('GOOGLE_CREDENTIALS_JSON')
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID') # "1K39pYj8UvHuyKhQA9MPianPlL6-JqMDZjTQEHKUnThg"
CALENDAR_ID = os.getenv('CALENDAR_ID', 'primary')

# --- 데이터 수집 엔진 ---

class StockDataUpdater:
    def __init__(self):
        self.creds = self._load_credentials()
        self.gc = gspread.authorize(self.creds)
        self.sh = self.gc.open_by_key(SPREADSHEET_ID)
        self.calendar_service = build('calendar', 'v3', credentials=self.creds)

    def _load_credentials(self):
        if CREDENTIALS_JSON:
            info = json.loads(CREDENTIALS_JSON)
            return Credentials.from_service_account_info(info, scopes=[
                'https://www.googleapis.com/auth/spreadsheets',
                'https://www.googleapis.com/auth/calendar'
            ])
        else:
            # 로컬 테스트용
            return Credentials.from_service_account_file('credentials.json', scopes=[
                'https://www.googleapis.com/auth/spreadsheets',
                'https://www.googleapis.com/auth/calendar'
            ])

    def get_kr_stocks(self, tickers):
        """한국 주식 데이터 수집"""
        data = []
        for ticker in tickers:
            try:
                # FinanceDataReader는 상장사 전체 리스트를 가져오기에 용이함
                # 실시간 가격은 yfinance나 별도 크롤링이 필요할 수 있음
                # 여기서는 간단히 yfinance (KRX 종목은 .KS 또는 .KQ) 사용
                sym = f"{ticker}.KS" if len(ticker) == 6 else ticker
                stock = yf.Ticker(sym)
                info = stock.fast_info
                hist = stock.history(period="2d")
                
                if len(hist) < 2: continue
                
                curr_price = hist['Close'].iloc[-1]
                prev_price = hist['Close'].iloc[-2]
                change = curr_price - prev_price
                change_rate = (change / prev_price) * 100
                
                data.append({
                    'Asset': 'KR',
                    'Ticker': ticker,
                    'Name': ticker, # 실제 이름은 별도 매핑 필요
                    'Price': round(curr_price, 2),
                    'Change': round(change, 2),
                    'ChangeRate': round(change_rate, 2),
                    'Volume': hist['Volume'].iloc[-1],
                    'Update': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                })
            except Exception as e:
                print(f"Error fetching KR stock {ticker}: {e}")
        return data

    def get_us_stocks(self, tickers):
        """미국 주식 데이터 수집"""
        data = []
        for ticker in tickers:
            try:
                stock = yf.Ticker(ticker)
                hist = stock.history(period="2d")
                if len(hist) < 2: continue
                
                curr_price = hist['Close'].iloc[-1]
                prev_price = hist['Close'].iloc[-2]
                change = curr_price - prev_price
                change_rate = (change / prev_price) * 100
                
                data.append({
                    'Asset': 'US',
                    'Ticker': ticker,
                    'Name': ticker,
                    'Price': round(curr_price, 2),
                    'Change': round(change, 2),
                    'ChangeRate': round(change_rate, 2),
                    'Volume': hist['Volume'].iloc[-1],
                    'Update': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                })
            except Exception as e:
                print(f"Error fetching US stock {ticker}: {e}")
        return data

    def get_crypto_data(self, tickers):
        """코인 데이터 수집 (BTC-USD, ETH-USD 등)"""
        data = []
        for ticker in tickers:
            try:
                sym = f"{ticker}-USD"
                stock = yf.Ticker(sym)
                hist = stock.history(period="2d")
                if len(hist) < 2: continue
                
                curr_price = hist['Close'].iloc[-1]
                prev_price = hist['Close'].iloc[-2]
                change = curr_price - prev_price
                change_rate = (change / prev_price) * 100
                
                data.append({
                    'Asset': 'Coin',
                    'Ticker': ticker,
                    'Name': ticker,
                    'Price': round(curr_price, 2),
                    'Change': round(change, 2),
                    'ChangeRate': round(change_rate, 2),
                    'Volume': hist['Volume'].iloc[-1],
                    'Update': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                })
            except Exception as e:
                print(f"Error fetching Crypto {ticker}: {e}")
        return data

    def get_news(self, ticker, asset_type):
        """종목 관련 뉴스 수집 (Naver/Yahoo Finance)"""
        news_list = []
        try:
            if asset_type == 'KR':
                url = f"https://finance.naver.com/item/news_news.naver?code={ticker}"
                headers = {'User-Agent': 'Mozilla/5.0'}
                resp = requests.get(url, headers=headers)
                soup = BeautifulSoup(resp.text, 'html.parser')
                titles = soup.select('.title a')
                for t in titles[:3]: # 상위 3개
                    link = "https://finance.naver.com" + t['href']
                    news_list.append(f"{t.text.strip()} ({link})")
            else:
                stock = yf.Ticker(ticker)
                for n in stock.news[:3]:
                    news_list.append(f"{n['title']} ({n['link']})")
        except Exception as e:
            print(f"Error fetching news for {ticker}: {e}")
        return "\n".join(news_list)

    def analyze_unusual_stocks(self, all_data):
        """특이 종목 분석 및 기록 (상한가 15%, 거래량 등)"""
        unusual_list = []
        for item in all_data:
            # 15% 이상 급등/급락 시 특이 종목으로 간주
            if abs(item['ChangeRate']) >= 15.0:
                news = self.get_news(item['Ticker'], item['Asset'])
                unusual_item = {
                    'Date': datetime.date.today().isoformat(),
                    'Asset': item['Asset'],
                    'Ticker': item['Ticker'],
                    'Name': item['Name'],
                    'ChangeRate': item['ChangeRate'],
                    'News': news
                }
                unusual_list.append(unusual_item)
        
        if unusual_list:
            try:
                ws = self.sh.worksheet('특이종목_테마')
                df = pd.DataFrame(unusual_list)
                ws.append_rows(df.values.tolist())
                print(f"Added {len(unusual_list)} unusual stocks.")
            except Exception as e:
                print(f"Error updating unusual stocks: {e}")
        return unusual_list

    def update_global_data(self, all_data):
        """글로벌데이터 시트 업데이트 (Overwrite)"""
        try:
            ws = self.sh.worksheet('글로벌데이터')
            ws.clear()
            # 헤더 순서 고정
            df = pd.DataFrame(all_data)
            df = df[['Asset', 'Ticker', 'Name', 'Price', 'Change', 'ChangeRate', 'Volume', 'Update']]
            ws.update([df.columns.values.tolist()] + df.values.tolist())
            print("Global data updated successfully.")
        except Exception as e:
            print(f"Error updating global data: {e}")

    def append_daily_history(self, summary_text):
        """일별기록 시트 추가 (Append)"""
        try:
            ws = self.sh.worksheet('일별기록')
            row = [datetime.date.today().isoformat(), summary_text]
            ws.append_row(row)
        except Exception as e:
            print(f"Error appending daily history: {e}")

    def create_calendar_event(self, title, description):
        """구글 캘린더 이벤트 생성"""
        event = {
            'summary': title,
            'description': description,
            'start': {
                'date': datetime.date.today().isoformat(),
                'timeZone': 'Asia/Seoul',
            },
            'end': {
                'date': datetime.date.today().isoformat(),
                'timeZone': 'Asia/Seoul',
            },
        }
        try:
            self.calendar_service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
            print("Calendar event created.")
        except Exception as e:
            print(f"Error creating calendar event: {e}")

# --- 실행 로직 ---
if __name__ == "__main__":
    # 필수 환경 변수 체크
    if not SPREADSHEET_ID:
        print("ERROR: SPREADSHEET_ID is missing.")
        exit(1)

    updater = StockDataUpdater()
    
    # 예시 티커 (실제 상용 시에는 시트에서 읽어오거나 확장 가능)
    kr_list = ['005930', '000660', '035720', '035420'] 
    us_list = ['AAPL', 'TSLA', 'NVDA', 'MSFT', 'GOOGL']
    coin_list = ['BTC', 'ETH', 'XRP', 'SOL']
    
    # 1. 데이터 수집
    print("Collecting data...")
    kr_data = updater.get_kr_stocks(kr_list)
    us_data = updater.get_us_stocks(us_list)
    coin_data = updater.get_crypto_data(coin_list)
    
    all_data = kr_data + us_data + coin_data
    
    # 2. 글로벌데이터 시트 갱신
    updater.update_global_data(all_data)
    
    # 3. 특이 종목 분석 및 뉴스 수집
    print("Analyzing unusual stocks...")
    unusual_stocks = updater.analyze_unusual_stocks(all_data)
    
    # 4. 일별 요약 및 캘린더 연동
    unusual_summary = "\n".join([f"- {s['Name']} ({s['ChangeRate']}%): {s['News'][:50]}..." for s in unusual_stocks])
    market_summary = f"KR: {len(kr_data)}, US: {len(us_data)}, Coin: {len(coin_data)}"
    full_summary = f"{market_summary}\n\n[Unusual Stocks]\n{unusual_summary}"
    
    updater.append_daily_history(full_summary)
    
    cal_title = f"[Stock Summary] Market: {market_summary}"
    updater.create_calendar_event(cal_title, full_summary)
    
    print("All processes completed.")
