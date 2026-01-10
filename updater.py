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
import argparse
from gspread_formatting import *

# --- 설정 및 상수 ---
APP_NAME = "DailyStockUpdater"
VERSION = "v1.4.0_20260110"

# 환경 변수 및 설정 (공백 제거 처리)
CREDENTIALS_JSON = os.getenv('GOOGLE_CREDENTIALS_JSON')
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID', '').strip()
CALENDAR_ID = os.getenv('CALENDAR_ID', 'primary').strip()

class StockDataUpdater:
    def __init__(self, target_date=None):
        self.creds = self._load_credentials()
        self.gc = gspread.authorize(self.creds)
        self.sh = self.gc.open_by_key(SPREADSHEET_ID)
        self.calendar_service = build('calendar', 'v3', credentials=self.creds)
        self.kr_name_map = self._get_kr_name_map()
        # 대상 날짜 설정 (기본값: 오늘)
        if isinstance(target_date, str):
            self.target_date = datetime.datetime.strptime(target_date, '%Y-%m-%d').date()
        else:
            self.target_date = target_date or datetime.date.today()

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
        """핵심 시장 지수 및 환율 수집 (대상 날짜 기준)"""
        indices = {
            '^KS11': 'KOSPI', '^KQ11': 'KOSDAQ', 
            '^GSPC': 'S&P500', '^IXIC': 'NASDAQ',
            'USDKRW=X': 'USD/KRW'
        }
        results = {}
        # target_date 포함 5일치 데이터를 가져와서 target_date 이하의 가장 최근 데이터 사용
        end_date = self.target_date + datetime.timedelta(days=1)
        start_date = self.target_date - datetime.timedelta(days=10)
        
        for ticker, name in indices.items():
            try:
                stock = yf.Ticker(ticker)
                hist = stock.history(start=start_date, end=end_date)
                if hist.empty: continue
                
                # target_date 이하의 가장 최신 행 찾기
                hist = hist[hist.index.date <= self.target_date]
                if len(hist) < 2: continue
                
                curr = hist.iloc[-1]
                prev = hist.iloc[-2]
                change = curr['Close'] - prev['Close']
                change_rate = (change / prev['Close']) * 100
                
                results[name] = {
                    'price': curr['Close'],
                    'change': change,
                    'rate': change_rate,
                    'date': curr.name.date().isoformat()
                }
            except Exception as e:
                print(f"Error fetching index {name}: {e}")
        return results

    def get_stock_data(self, tickers, asset_type):
        """주식/코인 상세 데이터 수집 (하이브리드 날짜 처리)"""
        data = []
        end_date = self.target_date + datetime.timedelta(days=1)
        # 20일 평균 거래량을 위해 여유 있게 40일치 수집
        start_date = self.target_date - datetime.timedelta(days=40)
        
        for ticker in tickers:
            try:
                sym = ticker
                if asset_type == 'KR':
                    sym = f"{ticker}.KS" if len(ticker) == 6 else ticker
                elif asset_type == 'Coin':
                    sym = f"{ticker}-USD"
                
                stock = yf.Ticker(sym)
                hist = stock.history(start=start_date, end=end_date)
                if hist.empty: continue
                
                # target_date 이하의 최신 데이터
                hist = hist[hist.index.date <= self.target_date]
                if len(hist) < 2: continue
                
                curr = hist.iloc[-1]
                prev = hist.iloc[-2]
                
                # 가상화폐인 경우, 만약 target_date에 데이터가 없으면 휴장일이 아니므로 데이터 부족으로 간주
                # (주식은 주말에 데이터가 없는 것이 정상임)
                if asset_type == 'Coin' and curr.name.date() < self.target_date:
                    print(f"Warning: Crypto {ticker} has no data exactly on {self.target_date}")

                avg_vol = hist['Volume'].iloc[:-1].tail(20).mean()
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
                    'News': self._get_news_top1(stock, ticker, asset_type),
                    'ActualDate': curr.name.date().isoformat()
                })
            except Exception as e:
                print(f"Error fetching {asset_type} {ticker}: {e}")
        return data

    def update_global_data(self, data, indices=None):
        """글로벌데이터 시트 갱신 및 서식 지정 (Price, Volume, MarketCap 우측 정렬)"""
        try:
            ws = self.sh.worksheet('글로벌데이터')
        except gspread.exceptions.WorksheetNotFound:
            ws = self.sh.add_worksheet(title='글로벌데이터', rows=100, cols=10)
        
        rows = []
        now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # 시장 요약 정보 추가 (상단)
        if indices:
            rows.append(['=== 📊 오늘의 시장 현황 ===', '', '', '', '', '', '', f'업데이트: {now_str}'])
            rows.append([])  # 빈 줄
            
            # Market Summary
            rows.append(['Market Summary', '', '', '', '', '', '', ''])
            rows.append(['KR', f"KOSPI {indices.get('KOSPI', {}).get('rate', 0):+.2f}%, KOSDAQ {indices.get('KOSDAQ', {}).get('rate', 0):+.2f}%", '', '', '', '', '', ''])
            rows.append(['US', f"S&P500 {indices.get('S&P500', {}).get('rate', 0):+.2f}%, NASDAQ {indices.get('NASDAQ', {}).get('rate', 0):+.2f}%", '', '', '', '', '', ''])
            rows.append(['환율', f"USD/KRW {indices.get('USD/KRW', {}).get('change', 0):+.1f}원", '', '', '', '', '', ''])
            rows.append([])  # 빈 줄
            
            # Indices Info
            rows.append(['Indices Info', '', '', '', '', '', '', ''])
            for k, v in indices.items():
                rows.append([k, f"{v['price']:,.1f}", f"{v['rate']:+.2f}%", '', '', '', '', ''])
            rows.append([])  # 빈 줄
            rows.append([])  # 빈 줄
        
        # 주요 종목 데이터 헤더 및 데이터
        rows.append(['=== 📈 주요 종목 데이터 ===', '', '', '', '', '', '', ''])
        header = ['Asset', 'Ticker', 'Name', 'Price', 'ChangeRate', 'Volume', 'MarketCap', 'Update']
        rows.append(header)
        
        for d in data:
            rows.append([
                d['Asset'], d['Ticker'], d['Name'], 
                d['FormattedPrice'], f"{d['ChangeRate']:+.2f}%", 
                self._format_large_number(d['Volume']), 
                self._format_large_number(d['MarketCap']), 
                now_str
            ])
        
        # 시트 업데이트 (gspread v6+ 대응: 명시적 인자 사용)
        ws.clear()
        ws.update(values=rows, range_name='A1')
        
        # 서식 지정 (D: Price, F: Volume, G: MarketCap 우측 정렬)
        try:
            fmt = CellFormat(horizontalAlignment='RIGHT')
            # 데이터 시작 행을 동적으로 계산 (indices가 있으면 더 아래에서 시작)
            data_start_row = len(rows) - len(data) + 1
            format_cell_range(ws, f'D{data_start_row}:D100', fmt)
            format_cell_range(ws, f'F{data_start_row}:G100', fmt)
            print("Successfully applied right-alignment to Price, Volume, MarketCap columns.")
        except Exception as e:
            print(f"Error applying formatting: {e}")

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
        """월별 탭 관리 및 반환 (target_date 기준)"""
        tab_name = self.target_date.strftime('%Y-%m')
        try:
            return self.sh.worksheet(tab_name)
        except gspread.exceptions.WorksheetNotFound:
            # 새 시트 생성 시에만 최신 구조 적용
            ws = self.sh.add_worksheet(title=tab_name, rows=1000, cols=15)
            ws.append_row(['Date', 'Market Summary', 'Watchlist Status', 'Unusual Stocks', 'Version'])
            print(f"✅ 새 월별 시트 생성: {tab_name}")
            return ws

    def process_and_report(self, manual_date=False):
        print(f"--- Running Updater (Initial Target: {self.target_date}) ---")
        
        print("1. 수집 중: 시장 지표 및 실제 거래일 확인...")
        indices = self.get_market_indices()
        
        # 실제 데이터 날짜 기반으로 target_date 자동 조정 (수동 입력이 아닐 경우)
        if not manual_date and indices:
            # 주요 지수(KOSPI, S&P500)의 날짜 중 가장 최근 것을 기준일로 채택
            dates = [v.get('date') for v in indices.values() if v.get('date')]
            if dates:
                actual_market_date = max(dates)
                if actual_market_date != self.target_date.isoformat():
                    print(f">>> Market Date Detected: {actual_market_date} (Changed from {self.target_date})")
                    self.target_date = datetime.datetime.strptime(actual_market_date, '%Y-%m-%d').date()

        print(f">>> Final Effective Date: {self.target_date}")
        
        print("2. 수집 중: 관심종목...")
        watchlist_raw = self.get_watchlist()
        watch_data = []
        for item in watchlist_raw:
            if not item.get('Ticker'): continue
            res = self.get_stock_data([str(item['Ticker'])], item.get('Asset', 'US'))
            if res:
                res[0]['Memo'] = item.get('Memo', '')
                watch_data.append(res[0])

        # 주말 체크 (토요일=5, 일요일=6)
        is_weekend = self.target_date.weekday() >= 5
        
        print("3. 수집 중: 주요 마켓 데이터...")
        market_all = []
        
        if is_weekend:
            print(">>> 주말 감지: 주식 시장 휴장, 코인 데이터만 수집합니다.")
            # 주말에는 코인만 수집 (24/7 거래)
            sample_crypto = ['BTC', 'ETH', 'XRP', 'SOL']  # 주요 코인
            market_all = self.get_stock_data(sample_crypto, 'Coin')
        else:
            # 평일: 주식 + 코인
            sample_kr = ['005930', '000660', '005380', '035420'] # 삼성전자, SK하이닉스, 현대차, NAVER
            sample_us = ['AAPL', 'TSLA', 'NVDA', 'MSFT'] # 애플, 테슬라, 엔비디아, 마이크로소프트
            market_all = self.get_stock_data(sample_kr, 'KR') + self.get_stock_data(sample_us, 'US')

        print("3.5. 갱신 중: 글로벌데이터 시트...")
        self.update_global_data(market_all, indices)

        unusual = [d for d in market_all if abs(d['ChangeRate']) >= 15.0 or d['VolSpike'] >= 3.0]


        # 요약 생성 - Market Summary (여러 줄 형식)
        market_summary_lines = []
        market_summary_lines.append(f"KR: KOSPI {indices.get('KOSPI', {}).get('rate', 0):+.2f}%, KOSDAQ {indices.get('KOSDAQ', {}).get('rate', 0):+.2f}%")
        market_summary_lines.append(f"US: S&P500 {indices.get('S&P500', {}).get('rate', 0):+.2f}%, NASDAQ {indices.get('NASDAQ', {}).get('rate', 0):+.2f}%")
        market_summary_lines.append(f"환율: USD/KRW {indices.get('USD/KRW', {}).get('change', 0):+.1f}원")
        market_summary = "\n".join(market_summary_lines)
        
        watch_summary = "\n".join([f"- {d['Name']} ({d['FormattedPrice']} / {d['ChangeRate']:+.2f}%): {d['Memo']}" for d in watch_data])
        
        # 주요 종목 요약 (모든 샘플 종목 표시)
        major_summary = "\n".join([f"- {d['Name']} ({d['FormattedPrice']} / {d['ChangeRate']:+.2f}%)" for d in market_all])
        # 특이 종목 요약
        unusual_summary = "\n".join([f"- {d['Name']} ({d['FormattedPrice']} / {d['ChangeRate']:+.2f}% / 거래량 {d['VolSpike']}배): {d['News']}" for d in unusual])

        print("4. 기록 중: 월별 일지 (중복 체크 포함)...")
        ws_monthly = self.get_monthly_worksheet()
        all_dates = ws_monthly.col_values(1)
        target_iso = self.target_date.isoformat()
        
        # 시트에는 주요 종목과 특이 종목을 합쳐서 기록
        detailed_market_info = f"[주요종목]\n{major_summary}\n\n[특이종목]\n{unusual_summary if unusual_summary else '없음'}"

        row_data = [
            target_iso,
            market_summary,
            watch_summary if watch_summary else "N/A",
            detailed_market_info,
            f"{APP_NAME} {VERSION}"
        ]


        if target_iso in all_dates:
            row_idx = all_dates.index(target_iso) + 1
            ws_monthly.update(values=[row_data], range_name=f'A{row_idx}:E{row_idx}')
            print(f"Updated existing row for {target_iso}.")
        else:
            ws_monthly.append_row(row_data)
            print(f"Appended new row for {target_iso}.")

        print("5. 연동 중: 구글 캘린더...")
        # 캘린더도 동일 날짜 중복 이벤트를 피하기 위해 제목에 날짜 포함
        if is_weekend:
            # 주말: 코인 정보만 표시
            cal_title = f"[{self.target_date}] 📅 주말 (주식 시장 휴장)"
            cal_desc = f"""## 🚫 주식 시장 휴장
오늘은 주말입니다. 한국 및 미국 주식 시장은 휴장입니다.

## ⭐ 관심종목 브리핑
{watch_summary if watch_summary else "등록된 관심종목이 없습니다."}

## 🪙 코인 시장 (24/7 거래)
{major_summary if market_all else "코인 데이터를 수집하지 못했습니다."}

## 🔗 상세 내용 보기
[구글 시트 바로가기](https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID})
"""
        else:
            # 평일: 전체 정보 표시
            cal_title = f"[{self.target_date}] 투자일지 KOSPI {indices.get('KOSPI',{}).get('rate',0):+.2f}%"
            cal_desc = f"""## ⭐ 관심종목 브리핑
{watch_summary if watch_summary else "등록된 관심종목이 없습니다."}

## 📈 핵심 시장 지표
- 국장: KOSPI {indices.get('KOSPI',{}).get('price',0):,.1f} ({indices.get('KOSPI',{}).get('rate',0):+.2f}%) / KOSDAQ {indices.get('KOSDAQ',{}).get('price',0):,.1f} ({indices.get('KOSDAQ',{}).get('rate',0):+.2f}%)
- 미장: S&P500 {indices.get('S&P500',{}).get('price',0):,.1f} ({indices.get('S&P500',{}).get('rate',0):+.2f}%) / NASDAQ {indices.get('NASDAQ',{}).get('price',0):,.1f} ({indices.get('NASDAQ',{}).get('rate',0):+.2f}%)
- 환율: USD/KRW {indices.get('USD/KRW',{}).get('price',0):,.1f} (전일대비 {indices.get('USD/KRW',{}).get('change',0):+.1f}원)

## 🏢 주요 종목 현황 (Market Leaders)
{major_summary}

## 🔥 실시간 특이종목 (거래량/변동성)
{unusual_summary if unusual_summary else "오늘의 특이종목이 없습니다."}

## 🔗 상세 내용 보기
[구글 시트 바로가기](https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID})
"""
        self.create_calendar_event(cal_title, cal_desc)
        print(f"모든 작업이 {self.target_date} 기준으로 완료되었습니다.")

    def create_calendar_event(self, title, description):
        # 마스킹 방지 및 오타 확인을 위한 상세 출력
        id_display = "|".join(list(CALENDAR_ID))
        print(f"DEBUG: CALENDAR_ID Raw Check: [{id_display}] (Length: {len(CALENDAR_ID)})")
        
        print("\n--- [Calendar Access Diagnostic] ---")
        try:
            # 1. 접근 가능한 캘린더 목록 출력
            cal_list = self.calendar_service.calendarList().list().execute()
            items = cal_list.get('items', [])
            accessible_ids = [item.get('id') for item in items]
            print(f"1. Accessible calendars: {accessible_ids}")

            # 1-1. 서비스 계정 본인의 'primary' 캘린더 정보 조회 테스트 (API 활성화 확인용)
            try:
                self.calendar_service.calendars().get(calendarId='primary').execute()
                print("2. SA's primary calendar access: SUCCESS (API is ENABLED)")
            except Exception as sa_e:
                print(f"2. SA's primary calendar access: FAILED ({str(sa_e)}) -> API might be DISABLED in GCP console.")

            # 2. CALENDAR_ID가 목록에 있는지 확인
            if CALENDAR_ID in accessible_ids:
                print(f"3. CALENDAR_ID '{CALENDAR_ID}' is in service account's list: YES")
            else:
                print(f"3. CALENDAR_ID '{CALENDAR_ID}' is NOT in list. Direct access test needed.")

            # 3. 직접 메타데이터 조회 (접근 가능 여부 최종 확인)
            print(f"4. Testing direct access to: {CALENDAR_ID}")
            try:
                cal_info = self.calendar_service.calendars().get(calendarId=CALENDAR_ID).execute()
                print(f"5. CALENDAR_ID '{CALENDAR_ID}' accessible: YES (Summary: {cal_info.get('summary')})")
            except Exception as get_e:
                print(f"5. Direct access to '{CALENDAR_ID}' FAILED: {str(get_e)}")
                
                # 4. 강제 구독 시도 (CalendarList.insert)
                print(f"6. Attempting to 'subscribe' (insert) calendar '{CALENDAR_ID}' to SA's list...")
                try:
                    self.calendar_service.calendarList().insert(body={'id': CALENDAR_ID}).execute()
                    print("7. Calendar subscription: SUCCESS! (Now it should be visible)")
                except Exception as ins_e:
                    print(f"7. Calendar subscription FAILED: {str(ins_e)}")
                    if "forbidden" in str(ins_e).lower():
                        print("HINT: This is a PERMISSION issue. Likely your domain admin (Workspace) blocks external sharing.")
                    elif "notFound" in str(ins_e).lower():
                        print("HINT: This is an ID/Typo issue. The calendar ID doesn't exist.")

        except Exception as e:
            print(f"DIAGNOSTIC_ERROR: Unexpected error during diagnostic: {str(e)}")
        print("------------------------------------\n")

        if CALENDAR_ID == 'primary':
            print("WARNING: CALENDAR_ID is set to 'primary'. This points to the Service Account's own calendar.")

        # 해당 날짜의 기존 이벤트 검색 및 삭제 (중복 방지)
        search_start = (self.target_date - datetime.timedelta(days=1)).isoformat() + "T00:00:00Z"
        search_end = (self.target_date + datetime.timedelta(days=2)).isoformat() + "T00:00:00Z"
        
        try:
            print(f"DEBUG: Searching events between {search_start} and {search_end}")
            events_result = self.calendar_service.events().list(
                calendarId=CALENDAR_ID, timeMin=search_start, timeMax=search_end,
                singleEvents=True, orderBy='startTime'
            ).execute()
            events = events_result.get('items', [])
            print(f"DEBUG: Found {len(events)} events in range.")
            
            for ev in events:
                ev_date = ev.get('start', {}).get('date')
                if ev_date == self.target_date.isoformat() and "투자일지" in ev.get('summary', ''):
                    print(f"DEBUG: Found matching event to delete: {ev.get('summary')} (ID: {ev.get('id')})")
                    self.calendar_service.events().delete(calendarId=CALENDAR_ID, eventId=ev['id']).execute()
                    print(f"Successfully deleted existing event.")

            # 새 이벤트 생성 준비
            next_day = (self.target_date + datetime.timedelta(days=1)).isoformat()
            
            event = {
                'summary': title,
                'description': description,
                'start': {'date': self.target_date.isoformat(), 'timeZone': 'Asia/Seoul'},
                'end': {'date': next_day, 'timeZone': 'Asia/Seoul'},
            }
            print(f"DEBUG: Inserting new event: {title} for date {self.target_date}")
            res = self.calendar_service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
            print(f"Successfully created calendar event! Link: {res.get('htmlLink')}")
            
        except Exception as e:
            print(f"ERROR: Failed to update calendar event: {str(e)}")
            if "Not Found" in str(e) or "404" in str(e):
                print(f"HINT: Calendar ID '{CALENDAR_ID}' not found.")
                print("--- Troubleshooting Checklist ---")
                print(f"1. Go to your Google Calendar settings for '{CALENDAR_ID}'.")
                print("2. Check 'Settings for my calendars' -> 'Share with specific people'.")
                print("3. Ensure you have added the Service Account as an editor:")
                # 서비스 계정 이메일 추출 시도
                sa_email = "your-service-account-email@..."
                try:
                    sa_info = json.loads(CREDENTIALS_JSON) if CREDENTIALS_JSON else {}
                    sa_email = sa_info.get('client_email', sa_email)
                except: pass
                print(f"   >>> {sa_email}")
                print("4. IMPORTANT: Make sure the permission is set to 'Make changes to events' (일정 변경).")
                print("5. Double check if the Calendar ID in GitHub Secrets has ANY typos (even one letter).")
                print("---------------------------------")
            elif "insufficientPermissions" in str(e):
                print("HINT: Insufficient permissions. Make sure the service account has 'Make changes to events' access.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("date", nargs="?", help="Target date (YYYY-MM-DD)", default=None)
    args = parser.parse_args()

    if not SPREADSHEET_ID:
        print("ERROR: SPREADSHEET_ID is missing.")
        exit(1)
    
    updater = StockDataUpdater(target_date=args.date)
    updater.process_and_report(manual_date=True if args.date else False)
