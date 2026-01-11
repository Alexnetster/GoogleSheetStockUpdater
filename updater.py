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

# 로컬 개발용 .env 파일 로드 (선택사항)
try:
    from dotenv import load_dotenv
    load_dotenv()  # .env 파일이 있으면 로드
    print("✅ .env 파일 로드 완료 (로컬 개발 모드)")
except ImportError:
    print("ℹ️ python-dotenv 미설치 (GitHub Actions 모드)")
except Exception as e:
    print(f"ℹ️ .env 파일 없음 또는 로드 실패: {e}")

# 네이버 증권 스크래핑 모듈
try:
    from scraper import scrape_volume_surge, scrape_price_limit, scrape_foreign_buy
    SCRAPER_AVAILABLE = True
except ImportError:
    print("Warning: scraper.py 모듈을 찾을 수 없습니다. 네이버 증권 데이터는 수집되지 않습니다.")
    SCRAPER_AVAILABLE = False

# --- 설정 및 상수 ---
APP_NAME = "DailyStockUpdater"
VERSION = "v2.4.2_20260111"

# 주의종목 기준 (자체 분석용 - 완화된 기준)
UNUSUAL_CHANGE_RATE = 10.0  # 변동률 기준 (%)
UNUSUAL_VOL_SPIKE = 2.0     # 거래량 급증 기준 (배수)
MIN_MARKET_CAP = 1000       # 최소 시가총액 (억원)

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
        if asset_type in ['KR', 'KRW']: 
            return f"₩{int(n):,}"
        return f"${n:,.2f}"

    def get_market_indices(self):
        """시장 지수 및 환율 수집 (시트 설정 기반 또는 기본값)"""
        # 1. 시트에서 Category='Index'인 항목 가져오기 시도
        index_map = {}
        try:
            ws = self.sh.worksheet('관심종목_관리')
            all_records = ws.get_all_records()
            for r in all_records:
                category = str(r.get('카테고리', r.get('Category', ''))).strip().lower()
                if category in ['index', '지수']:
                    ticker = str(r.get('티커', r.get('Ticker', ''))).strip()
                    name = str(r.get('종목명', r.get('Name', ''))).strip()
                    if ticker and name:
                        index_map[ticker] = name
        except:
            pass

        if not index_map:
            print("⚠️  [필독] 시트에서 'Index' 또는 '지수' 카테고리를 찾을 수 없습니다.")
            print("   -> 'tools/initialize_sheet.py'를 실행하거나 GitHub 'Maintenance' 워크플로우를 통해 시트를 복구하세요.")
        else:
            print(f">>> Found {len(index_map)} indices in sheet.")

        results = {}
        end_date = self.target_date + datetime.timedelta(days=1)
        start_date = self.target_date - datetime.timedelta(days=10)
        
        for ticker, name in index_map.items():
            try:
                stock = yf.Ticker(ticker)
                hist = stock.history(start=start_date, end=end_date, prepost=True)
                if hist.empty: continue
                
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
                print(f"Error fetching index {name} ({ticker}): {e}")
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
                hist = stock.history(start=start_date, end=end_date, prepost=True)
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

                # 테마 및 정보 링크 수집
                theme = "-"
                info_link = ""
                
                try:
                    if asset_type == 'US':
                        sector = stock.info.get('sector', '')
                        industry = stock.info.get('industry', '')
                        theme = f"{sector} / {industry}" if sector and industry else sector or industry or "-"
                        info_link = f"https://finance.yahoo.com/quote/{ticker}"
                    elif asset_type == 'KR':
                        # 네이버 크롤링으로 테마 및 링크 수집
                        url = f"https://finance.naver.com/item/main.naver?code={ticker}"
                        info_link = f"https://www.judal.co.kr/search?q={ticker}" # 주달 링크 우선 사용
                        
                        resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
                        soup = BeautifulSoup(resp.text, 'html.parser')
                        
                        # 테마 추출 시도 (관련 섹션/테마 영역)
                        theme_links = soup.select('div.aside_area div.popular_list a') # 실시간 검색 테마가 아닌 해당 종목 테마 찾기
                        # 실제 해당 종목 테마는 페이지 하단이나 우측에 '테마명'으로 나오기도 함. 
                        # 간단히 '테마' 텍스트를 포함한 링크나 텍스트 검색
                        themes = []
                        for a in soup.find_all('a', href=True):
                            if '/sise/theme.naver?field=name' in a['href']:
                                themes.append(a.text.strip())
                        if themes:
                            theme = ", ".join(list(set(themes))[:3]) # 중복 제거 및 최대 3개
                except:
                    pass

                # 전문가 의견/추천 정보 수집
                recommendation = "-"
                
                try:
                    if asset_type == 'US':
                        rec_key = stock.info.get('recommendationKey', '-')
                        target_price = stock.info.get('targetMeanPrice')
                        if rec_key != '-':
                            recommendation = f"미국:{rec_key.upper()}"
                            if target_price:
                                recommendation += f" (T:${target_price})"
                    elif asset_type == 'KR':
                        # 투자의견 및 목표주가 (rt_invest_box 내)
                        invest_box = soup.select_one('.rt_invest_box')
                        if invest_box:
                            opinion = invest_box.select_one('em.invest_point')
                            target = invest_box.select_one('em.num')
                            if opinion: recommendation = f"국장:{opinion.text.strip()}"
                            if target: recommendation += f" (T:₩{target.text.strip()})"
                except:
                    pass

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
                    'Theme': theme,
                    'InfoLink': info_link,
                    'News': self._get_news_top1(stock, ticker, asset_type),
                    'ActualDate': curr.name.date().isoformat(),
                    'Recommendation': recommendation,
                    'ExpertOpinion': ""
                })
            except Exception as e:
                print(f"Error fetching {asset_type} {ticker}: {e}")
        return data

    def update_global_data(self, data, indices=None, is_kr_open=True, is_us_open=True):
        """'오늘' 시트 갱신 및 UI 개선 (휴장 안내, 요일 포함 시간 표시)"""
        sheet_name = '오늘'
        try:
            ws = self.sh.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            ws = self.sh.add_worksheet(title=sheet_name, rows=100, cols=10)
        
        rows = []
        
        # 요일 및 시간 포맷팅
        weekdays = ["월", "화", "수", "목", "금", "토", "일"]
        now = datetime.datetime.now()
        now_full_str = now.strftime('%Y-%m-%d') + f" ({weekdays[now.weekday()]}) " + now.strftime('%H:%M:%S')
        
        target_date_str = self.target_date.strftime('%Y-%m-%d') + f" ({weekdays[self.target_date.weekday()]})"
        
        # 내일 개장 여부 판단 (단순 주말 체크)
        tomorrow = self.target_date + datetime.timedelta(days=1)
        is_kr_open_tomorrow = (tomorrow.weekday() < 5)
        is_us_open_tomorrow = (tomorrow.weekday() < 5)
        
        # 시장 상태 요약 행
        status_lines = []
        kr_today = "개장" if is_kr_open else "휴장"
        kr_tmrw = "개장" if is_kr_open_tomorrow else "휴장"
        us_today = "개장" if is_us_open else "휴장"
        us_tmrw = "개장" if is_us_open_tomorrow else "휴장"
        
        status_lines.append(f"기준 일자: {target_date_str} [국장 오늘 {kr_today}/내일 {kr_tmrw}][미장 오늘 {us_today}/내일 {us_tmrw}]")
        status_lines.append(f"업데이트: {now_full_str}")
        
        rows.append(['=== 📅 시장 현황 및 업데이트 ===', '', '', '', '', '', ''])
        for line in status_lines:
            rows.append([line, '', '', '', '', '', ''])
        rows.append([]) # 빈 줄
        
        # 시장 지표 섹션
        if indices:
            rows.append(['=== 📊 지수 및 환율 ===', '', '', '', '', '', ''])
            summary_header = ['번호', '국가', '거래소', '지수', '변동률', '', '']
            rows.append(summary_header)
            
            idx_num = 1
            for k, v in indices.items():
                if k in ['KOSPI', 'KOSDAQ']:
                    country, exchange = "한국", k
                elif k in ['S&P500', 'NASDAQ']:
                    country, exchange = "미국", k
                elif k == 'USD/KRW':
                    country, exchange = "환율", "USD/KRW"
                else:
                    country, exchange = "-", k
                
                price_str = f"{v['price']:,.1f}"
                rate_str = f"{v['rate']:+.2f}%"
                rows.append([str(idx_num), country, exchange, price_str, rate_str, '', ''])
                idx_num += 1
            
            rows.append([])
        
        # 주요 종목 데이터 섹션 (구분: Major vs Watchlist)
        categories_to_display = [('Major', '주요종목'), ('Watchlist', '관심종목')]
        for cat_id, cat_name in categories_to_display:
            # 보강된 필터링: blank, '관심종목' -> Watchlist / '주요종목' -> Major
            def is_match(d_cat, target_id):
                clean_cat = str(d_cat).strip().lower()
                if not clean_cat: clean_cat = 'watchlist' # 기본값
                
                # 한글 매핑
                mapping = {'지수': 'index', '주요종목': 'major', '관심종목': 'watchlist'}
                translated_cat = mapping.get(clean_cat, clean_cat)
                
                return translated_cat == target_id.lower()

            cat_data = [d for d in data if is_match(d.get('Category', ''), cat_id)]
            if not cat_data: continue
            
            rows.append([f'=== [{cat_name}] ===', '', '', '', '', '', ''])
            header = ['자산', '티커', '종목명', '현재가', '변동률', '거래량', '시총']
            rows.append(header)
            
            for d in cat_data:
                rows.append([
                    d['Asset'], d['Ticker'], d['Name'], 
                    d['FormattedPrice'], f"{d['ChangeRate']:+.2f}%", 
                    self._format_large_number(d['Volume']), 
                    self._format_large_number(d['MarketCap'])
                ])
            rows.append([]) # 섹션 간 빈 줄
        
        # 시트 업데이트 (gspread v6+ 대응: 명시적 인자 사용)
        ws.clear()
        ws.update(values=rows, range_name='A1')
        
        # 서식 지정
        try:
            fmt_right = CellFormat(horizontalAlignment='RIGHT')
            fmt_left = CellFormat(horizontalAlignment='LEFT')
            
            # 지수 섹션 서식 (지수가 있을 때만)
            if indices:
                # 시장 상태 섹션 높이 (헤더 + 3줄 상태 안내 + 빈줄) = 5
                status_rows = 5 
                # 지수 헤더는 status_rows + 1, 데이터는 status_rows + 2부터
                summary_start = status_rows + 2
                summary_end = summary_start + len(indices) - 1
                
                format_cell_range(ws, f'B{summary_start}:C{summary_end}', fmt_left)
                format_cell_range(ws, f'D{summary_start}:E{summary_end}', fmt_right)
            
            # 주요 종목 데이터 정렬
            data_start_row = len(rows) - len(data) + 1
            format_cell_range(ws, f'D{data_start_row}:G200', fmt_right)
            
            print(f"Successfully applied formatting to '{sheet_name}' sheet.")
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
        """관심종목_요청 기반으로 관심종목_관리 동기화 및 데이터 반환"""
        try:
            # 1. 관심종목_요청 시트 로드 (없으면 생성)
            try:
                rq_ws = self.sh.worksheet('관심종목_요청')
            except gspread.exceptions.WorksheetNotFound:
                # 구버전 호환 또는 신규 생성
                rq_ws = self.sh.add_worksheet(title='관심종목_요청', rows=100, cols=6)
                rq_ws.append_row(['티커', '종목명', '카테고리', '메모', '사용여부', '검색결과'])
                print("Created '관심종목_요청' sheet.")
            
            requests = rq_ws.get_all_records()
            # 한글/영어 키 모두 대응 (과도기 지원)
            def get_val(r, kor, eng): return r.get(kor, r.get(eng, ''))

            # 2. 관심종목_관리 시트 로드 (없으면 생성)
            try:
                mgmt_ws = self.sh.worksheet('관심종목_관리')
            except gspread.exceptions.WorksheetNotFound:
                mgmt_ws = self.sh.add_worksheet(title='관심종목_관리', rows=100, cols=10)
                mgmt_ws.append_row(['구분', '카테고리', '티커', '종목명', '테마', '메모', '알림가', '시스템추천', '전문가의견', '정보링크'])
            
            current_mgmt = mgmt_ws.get_all_records()
            mgmt_tickers = [str(r.get('티커', r.get('Ticker', ''))) for r in current_mgmt]
            
            # 3. 요청 처리 및 동기화
            final_watchlist = []
            
            for i, req in enumerate(requests):
                ticker = str(get_val(req, '티커', 'Ticker')).strip()
                name_req = str(get_val(req, '종목명', 'Name')).strip()
                enabled = str(get_val(req, '사용여부', 'RequestEnabled')).upper() == 'TRUE'
                category = str(get_val(req, '카테고리', 'Category')).strip()
                memo = get_val(req, '메모', 'Memo')
                
                if not (ticker or name_req): continue
                
                found_ticker = None
                asset_type = 'US'
                
                # TickerFound 필드 인덱스 찾기
                headers = rq_ws.row_values(1)
                found_col_idx = 6 # 기본값
                if '검색결과' in headers: found_col_idx = headers.index('검색결과') + 1
                elif 'TickerFound' in headers: found_col_idx = headers.index('TickerFound') + 1

                # Ticker/Name으로 종목 찾기
                if ticker:
                    try:
                        if ticker.isdigit() and len(ticker) == 6: # 한국 주식
                            asset_type = 'KR'
                            if ticker in self.kr_name_map: found_ticker = ticker
                        elif '-' in ticker: # 코인 (예: BTC-USD)
                            asset_type = 'Coin'
                            found_ticker = ticker
                        else:
                            # 미국 주식
                            s = yf.Ticker(ticker)
                            if s.info.get('symbol'): found_ticker = ticker
                    except: pass
                
                if not found_ticker and name_req:
                    for code, name in self.kr_name_map.items():
                        if name == name_req:
                            found_ticker = code
                            asset_type = 'KR'
                            break
                
                found_status = 'TRUE' if found_ticker else 'FALSE'
                rq_ws.update_cell(i + 2, found_col_idx, found_status)
                
                if enabled and found_ticker:
                    # 현재 관리 시트에서 기존 정보(알림가 등) 유지용 데이터 찾기
                    existing_item = next((r for r in current_mgmt if str(r.get('티커', r.get('Ticker', ''))) == found_ticker), {})
                    
                    final_watchlist.append({
                        'Ticker': found_ticker,
                        'Asset': asset_type,
                        'Memo': memo,
                        'Category': category,
                        'AlertPrice': existing_item.get('알림가', existing_item.get('Alert_Price', '')),
                        'ExpertOpinion': existing_item.get('전문가의견', existing_item.get('Expert_Opinion', ''))
                    })
                    
                    if found_ticker not in mgmt_tickers:
                        name_display = self.kr_name_map.get(found_ticker, name_req or found_ticker)
                        # 새 필드 구조 적용: 구분, 카테고리, 티커, 종목명, 테마, 메모, 알림가, 시스템추천, 전문가의견, 정보링크
                        mgmt_ws.append_row([
                            asset_type, category, found_ticker, name_display, "", memo, "", "", "", ""
                        ])
                        print(f"Added to management: {found_ticker}")
                else:
                    if ticker and ticker in mgmt_tickers:
                        # 컬럼 3(티커) 또는 2(Ticker)에서 검색
                        ticker_col = 3 if '티커' in mgmt_ws.row_values(1) else 2
                        cells = mgmt_ws.findall(ticker, in_column=ticker_col)
                        for cell in cells:
                            mgmt_ws.delete_rows(cell.row)
                            print(f"Removed from management: {ticker}")
                rq_ws.update_cell(i + 2, found_col_idx, found_status)

            return mgmt_ws.get_all_records()

        except Exception as e:
            print(f"Error in Watchlist Sync: {e}")
            # 에러 발생 시 기존 방식대로라도 시도
            try:
                ws = self.sh.worksheet('관심종목_관리')
                return ws.get_all_records()
            except:
                return []

    def get_monthly_worksheet(self):
        """월별 탭 관리 및 반환 (target_date 기준)"""
        tab_name = self.target_date.strftime('%Y-%m')
        headers = ['날짜', '시장 요약', '관심종목 현황', '주의종목', '버전']
        
        try:
            ws = self.sh.worksheet(tab_name)
            
            # 기존 시트 서식 적용 (상단 정렬)
            try:
                fmt_top = CellFormat(verticalAlignment='TOP')
                format_cell_range(ws, 'A:E', fmt_top)
            except: pass
            
            return ws
        except gspread.exceptions.WorksheetNotFound:
            # 새 시트 생성 시에만 최신 구조 적용
            ws = self.sh.add_worksheet(title=tab_name, rows=1000, cols=15)
            ws.update('A1', [headers])
            
            # 모든 컬럼 상단 정렬 적용
            try:
                fmt_top = CellFormat(verticalAlignment='TOP')
                format_cell_range(ws, 'A:E', fmt_top)  # 모든 컬럼 상단 정렬
                print(f"✅ 새 월별 시트 생성 및 서식 적용: {tab_name}")
            except Exception as e:
                print(f"✅ 새 월별 시트 생성: {tab_name} (서식 적용 실패: {e})")
            
            return ws

    def process_and_report(self, mode="AUTO", manual_date=False):
        print(f"--- Running Updater (Mode: {mode}, Target: {self.target_date}) ---")
        
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
        
        # 실제 데이터 존재 여부로 시장 개장 여부 판단
        kospi_date = indices.get('KOSPI', {}).get('date')
        sp500_date = indices.get('S&P500', {}).get('date')
        
        is_kr_open = (kospi_date == self.target_date.isoformat())
        is_us_open = (sp500_date == self.target_date.isoformat())
        
        # 모드별 수집 시장 강제 조정
        if mode == "MORNING":
            is_us_open = True if sp500_date else False # 전일 미장 마감 데이터
            is_kr_open = True # 국장 개장 준비
            print(">>> [MORNING Mode] Capturing US Close & KR Open")
        elif mode == "MIDDAY":
            is_kr_open = True
            is_us_open = False # 미장 휴식
            print(">>> [MIDDAY Mode] Capturing KR Morning Session")
        elif mode == "CLOSE":
            is_kr_open = True
            is_us_open = False
            print(">>> [CLOSE Mode] Capturing KR Final Results")
        elif mode == "EVENING":
            is_kr_open = True # NXT 마감
            is_us_open = True # 미장 프리마켓
            print(">>> [EVENING Mode] Capturing NXT Close & US Pre-market")
        else:
            # AUTO 모드/기존 로직: NXT 시간대(08:00~20:00) 및 평일 고려 보정
            now_kst = datetime.datetime.now()
            if now_kst.weekday() < 5:  # 평일
                # 08:00~20:00 사이에는 무조건 국장 데이터를 활성화 (NXT)
                if 8 <= now_kst.hour < 20:
                    is_kr_open = True
                    print(">>> NXT Active Session detected (08:00-20:00 KST)")
                
                # 08:00~10:00 사이 (오전 브리핑)에는 전일 미장 마감 데이터를 수집하도록 허용
                if 8 <= now_kst.hour <= 10:
                    if sp500_date: # indices에 미장 데이터가 있으면
                        is_us_open = True
                        print(">>> US market session (just closed) detected for Morning Briefing")

        print(f">>> 시장 개장 상태: 한국={is_kr_open}, 미국={is_us_open}")

        print("2. 수집 중: 관심종목 (개장된 시장만)...")
        watchlist_raw = self.get_watchlist()
        watch_data = [] # 개인 관심종목
        major_data = [] # 주요 종목 (지수 옆 표시용)
        
        for item in watchlist_raw:
            ticker = str(item.get('Ticker', ''))
            if not ticker: continue
            
            category = str(item.get('Category', '')).strip().lower()
            if category in ['index', '지수']: continue # 지수는 이미 수집됨
            
            asset_type = item.get('Asset', 'US')
            
            # 시장 개장 상태에 따른 필터링 (코인은 항상 수집)
            if asset_type == 'KR' and not is_kr_open: continue
            if asset_type == 'US' and not is_us_open: continue
            
            res = self.get_stock_data([ticker], asset_type)
            if res:
                # 시트의 사용자 메모 및 의견 병합
                res[0]['Memo'] = item.get('Memo', '')
                res[0]['ExpertOpinion_User'] = item.get('Expert_Opinion', '')
                res[0]['Category'] = item.get('Category', 'Watchlist')
                
                # 리포트용 요약 문구 생성
                final_rec = item.get('Recommendation', res[0].get('Recommendation', '-'))
                res[0]['FinalRecommendation'] = final_rec
                
                if category in ['major', '주요']:
                    major_data.append(res[0])
                else:
                    watch_data.append(res[0])
        
        # '오늘' 시트 및 리포트용 데이터 통합
        market_all = major_data + watch_data 

        print("3.5. 갱신 중: '오늘' 시트...")
        self.update_global_data(market_all, indices, is_kr_open, is_us_open)

        print("4. 기록 중: '관심종목_관리' 종목 정보 업데이트...")
        # 수집된 최신 정보(가격, 추천 등)를 관리 시트에 반영
        try:
            mgmt_ws = self.sh.worksheet('관심종목_관리')
            mgmt_data = mgmt_ws.get_all_records()
            for i, row_dict in enumerate(mgmt_data):
                ticker = str(row_dict.get('티커', row_dict.get('Ticker', '')))
                # market_all에서 해당 티커 찾기
                info = next((d for d in market_all if d['Ticker'] == ticker), None)
                if info:
                    row_idx = i + 2
                    # 필드 맵핑 (구분, 카테고리, 티커, 종목명, 테마, 메모, 알림가, 시스템추천, 전문가의견, 정보링크)
                    # 영어/한글 혼용 대응을 위해 인덱스 기반 업데이트 권장
                    headers = mgmt_ws.row_values(1)
                    try:
                        if '테마' in headers: mgmt_ws.update_cell(row_idx, headers.index('테마') + 1, info.get('Theme', '-'))
                        if '시스템추천' in headers: mgmt_ws.update_cell(row_idx, headers.index('시스템추천') + 1, info.get('Recommendation', '-'))
                        if '정보링크' in headers: mgmt_ws.update_cell(row_idx, headers.index('정보링크') + 1, info.get('InfoLink', ''))
                    except Exception as e:
                        print(f"Error updating mgmt row for {ticker}: {e}")
        except Exception as e:
            print(f"Warning: 관심종목_관리 업데이트 중 실패: {e}")

        print("4. 수집 중: 주의종목 (네이버 증권 + FDR 전수 조사 + 자체 분석)...")
        
        # 4-1. 네이버 증권 스크래핑 (실시간/핫 종목)
        naver_unusual = []
        if SCRAPER_AVAILABLE and is_kr_open:
            try:
                # 한국 시장 전수 조사 (FDR 사용)
                # 상한가 전수 조사 및 고거래량(500만 이상) 종목 추출
                from scraper import analyze_market_fdr
                print(">>> 시장 전수 조사 중 (FDR)...")
                fdr_res = analyze_market_fdr(min_volume=5000000)
                
                # FDR 결과 병합
                naver_unusual.extend(fdr_res['upper'])
                naver_unusual.extend(fdr_res['volume'])
                
                # 네이버 실시간 스크래핑 (외국인 순매수 등은 네이버가 정확)
                print(">>> 네이버 실시간 스크래핑 중...")
                naver_unusual.extend(scrape_foreign_buy(max_items=10))
                
                # 장중 실시간 거래량/상한가 수집
                naver_unusual.extend(scrape_volume_surge(max_items=5))
                naver_unusual.extend(scrape_price_limit(max_items=5))
                    
                print(f">>> 수집 완료: 전수조사 및 스크래핑 총 {len(naver_unusual)}개")
            except Exception as e:
                print(f"Warning: 스크래핑/전수조사 중 실패: {e}")
        elif not is_kr_open:
            print(">>> 휴장일: 국내 주식 주의종목 수집을 건너뜁니다.")
        
        # 4-2. 자체 분석 (보유 종목/관심 종목 대상 완화된 기준)
        internal_unusual = [
            d for d in market_all 
            if (abs(d['ChangeRate']) >= UNUSUAL_CHANGE_RATE or d.get('VolSpike', 0) >= UNUSUAL_VOL_SPIKE)
            and d.get('MarketCap', 0) >= MIN_MARKET_CAP * 100_000_000  # 억원 → 원
        ]
        # Source 태그 추가
        for d in internal_unusual:
            d['Source'] = 'Internal'
        
        print(f">>> 자체 분석: {len(internal_unusual)}개 종목 추출 완료")
        
        # 4-3. 데이터 병합 및 중복 제거 (네이버/FDR 우선)
        all_unusual = naver_unusual + internal_unusual
        seen = set()
        unusual = []
        for d in all_unusual:
            if d['Ticker'] not in seen:
                unusual.append(d)
                seen.add(d['Ticker'])
        
        print(f">>> 최종 주의종목: {len(unusual)}개 (외부수집 {len(naver_unusual)}개 + 자체 {len(unusual) - len(naver_unusual)}개)")


        # 요약 생성 - Market Summary (지수 동적 생성)
        market_summary_lines = []
        # 지수들을 국가별로 묶어서 표시 시도
        kr_indices = [f"{k} {v['rate']:+.2f}%" for k, v in indices.items() if k in ['KOSPI', 'KOSDAQ']]
        us_indices = [f"{k} {v['rate']:+.2f}%" for k, v in indices.items() if k in ['S&P500', 'NASDAQ', 'Dow Jones']]
        other_indices = [f"{k} {v['price']:,.1f} ({v['change']:+.1f})" for k, v in indices.items() if k not in ['KOSPI', 'KOSDAQ', 'S&P500', 'NASDAQ', 'Dow Jones']]

        if kr_indices: market_summary_lines.append(f"KR: {', '.join(kr_indices)}")
        if us_indices: market_summary_lines.append(f"US: {', '.join(us_indices)}")
        if other_indices: market_summary_lines.append(f"기타: {', '.join(other_indices)}")
        
        market_summary = "\n".join(market_summary_lines) or "N/A"
        
        # 관심종목 요약 (추천 정보 포함 및 형식 지정)
        watch_summary_lines = []
        for d in watch_data:
            # 국가별 명칭 형식 지정 (KR:이름 / US:[Ticker])
            if d['Asset'] == 'KR':
                display_name = f"KR:{d['Name']}"
            elif d['Asset'] == 'US':
                display_name = f"US:[{d['Ticker']}]"
            else:
                display_name = f"[{d['Ticker']}] {d['Name']}"

            rec_part = f" [{d['FinalRecommendation']}]" if d['FinalRecommendation'] != "-" else ""
            memo_part = f": {d['Memo']}" if d['Memo'] else ""
            user_opinion = f" (의견: {d['ExpertOpinion_User']})" if d['ExpertOpinion_User'] else ""
            watch_summary_lines.append(f"- {display_name} ({d['FormattedPrice']} / {d['ChangeRate']:+.2f}%){rec_part}{memo_part}{user_opinion}")
        
        watch_summary = "\n".join(watch_summary_lines)
        
        unusual_summary_lines = []
        # 1. [주요종목] 섹션 (시트에서 'Major'로 태깅된 것만 표시)
        major_list = [d for d in market_all if str(d.get('Category', '')).lower() == 'major']
        if major_list:
            asset_types = [('KR', '한국'), ('US', '미국'), ('Coin', '코인')]
            for a_code, a_name in asset_types:
                subset = [d for d in major_list if d.get('Asset') == a_code and d.get('Price', 0) > 0]
                if subset:
                    unusual_summary_lines.append(f"[주요종목:{a_name}]")
                    for d in subset:
                        unusual_summary_lines.append(f"[{d['Ticker']}] {d['Name']} ({d['FormattedPrice']} / {d['ChangeRate']:+.2f}%)")
                    unusual_summary_lines.append("")

        # 2. [주의종목:네이버증권]
        if unusual:
            naver_total = [d for d in unusual if d.get('Source') in ['Naver', 'FDR'] and d.get('Price', 0) > 0]
            if naver_total:
                unusual_summary_lines.append("[주의종목:네이버증권]")
                for region in ['한국', '미국']:
                    asset_prefix = 'KR' if region == '한국' else 'US'
                    region_stocks = [d for d in naver_total if d.get('Asset') == asset_prefix]
                    if region_stocks:
                        unusual_summary_lines.append(f"<{region}>")
                        for d in region_stocks[:10]:
                            category = d.get('Category', '기타')
                            unusual_summary_lines.append(f"[{category}] {d['Name']} ({d['FormattedPrice']} / {d['ChangeRate']:+.2f}%)")
                unusual_summary_lines.append("")
            
            # 3. [주의종목:자체분석]
            internal_total = [d for d in unusual if d.get('Source') == 'Internal' and d.get('Price', 0) > 0]
            if internal_total:
                unusual_summary_lines.append("[주의종목:자체분석]")
                for region in ['한국', '미국']:
                    asset_prefix = 'KR' if region == '한국' else 'US'
                    region_stocks = [d for d in internal_total if d.get('Asset') == asset_prefix]
                    if region_stocks:
                        unusual_summary_lines.append(f"<{region}>")
                        for d in region_stocks[:5]:
                            reason = "급등락" if abs(d['ChangeRate']) >= UNUSUAL_CHANGE_RATE else "거래량급증"
                            unusual_summary_lines.append(f"[{reason}] {d['Name']} ({d['FormattedPrice']} / {d['ChangeRate']:+.2f}%)")
        
        unusual_summary = "\n".join(unusual_summary_lines) if unusual_summary_lines else "N/A"

        print("5. 기록 중: 월별 일지 (중복 체크 포함)...")
        ws_monthly = self.get_monthly_worksheet()
        all_dates = ws_monthly.col_values(1)
        target_iso = self.target_date.isoformat()
        
        # 시트에는 주의종목만 기록 (네이버 우선 형식)
        detailed_market_info = unusual_summary

        row_data = [
            target_iso,
            market_summary,
            watch_summary if watch_summary else "N/A",
            detailed_market_info,
            f"{APP_NAME} {VERSION}"
        ]


        if target_iso in all_dates:
            row_idx = all_dates.index(target_iso) + 1
            existing_row = ws_monthly.row_values(row_idx)
            
            # 데이터 병합 로직 (기존 데이터가 "N/A"가 아니면 유지하고 현재 데이터와 병합)
            # Market Summary 병합
            new_market = row_data[1]
            old_market = existing_row[1] if len(existing_row) > 1 else ""
            merged_market = self._merge_report_sections(old_market, new_market, ["KR:", "US:", "환율:"])
            
            # Watchlist Status 병합
            new_watch = row_data[2]
            old_watch = existing_row[2] if len(existing_row) > 2 else ""
            merged_watch = self._merge_report_sections(old_watch, new_watch, ["KR:", "US:", "Coin:"])
            
            # Unusual Stocks 병합 (주의종목)
            new_unusual = row_data[3]
            old_unusual = existing_row[3] if len(existing_row) > 3 else ""
            merged_unusual = self._merge_report_sections(old_unusual, new_unusual, ["[주요종목:한국]", "[주요종목:미국]", "[주요종목:코인]", "[주의종목:네이버증권]", "[주의종목:자체분석]"])

            row_data[1] = merged_market
            row_data[2] = merged_watch
            row_data[3] = merged_unusual
            
            ws_monthly.update(values=[row_data], range_name=f'A{row_idx}:E{row_idx}')
            print(f"Updated and Merged row for {target_iso}.")
        else:
            ws_monthly.append_row(row_data)
            print(f"Appended new row for {target_iso}.")

    def _merge_report_sections(self, old_text, new_text, markers):
        """섹션 마커를 기준으로 구정보와 신정보 병합 (신정보 우선 업데이트)"""
        if not old_text or old_text == "N/A": return new_text
        if not new_text or new_text == "N/A": return old_text
        
        # 1. 기존 텍스트를 섹션별로 분리
        def split_by_markers(text, markers):
            sections = {}
            current_marker = "DEFAULT"
            lines = text.split('\n')
            for line in lines:
                found_marker = False
                for m in markers:
                    if line.startswith(m):
                        current_marker = m
                        sections[current_marker] = [line]
                        found_marker = True
                        break
                if not found_marker:
                    if current_marker not in sections: sections[current_marker] = []
                    sections[current_marker].append(line)
            return {k: "\n".join(v).strip() for k, v in sections.items()}

        old_sections = split_by_markers(old_text, markers)
        new_sections = split_by_markers(new_text, markers)
        
        # 2. 새로운 섹션 정보로 덮어쓰거나 기존 유지
        merged = old_sections.copy()
        for k, v in new_sections.items():
            if v and v != "N/A":
                merged[k] = v
        
        # 3. 마커 순서대로 재조립
        ordered_parts = []
        if "DEFAULT" in merged and merged["DEFAULT"]:
            ordered_parts.append(merged["DEFAULT"])
        for m in markers:
            if m in merged and merged[m]:
                ordered_parts.append(merged[m])
        
        return "\n\n".join(ordered_parts).strip()

        print("5. 연동 중: 구글 캘린더...")
        # 캘린더 이벤트 제목 및 내용 생성
        
        # 시간대별 세션 태그
        now_kst = datetime.datetime.now()
        session_tag = ""
        if mode == "MORNING" or (7 <= now_kst.hour <= 9): session_tag = " (모닝브리핑) "
        elif mode == "MIDDAY" or (11 <= now_kst.hour <= 13): session_tag = " (미드데이브리핑) "
        elif mode == "CLOSE" or (15 <= now_kst.hour <= 17): session_tag = " (장마감리뷰) "
        elif mode == "EVENING" or (19 <= now_kst.hour <= 21): session_tag = " (이브닝브리핑) "

        # 시장 개장 상태에 따라 제목 결정
        if is_kr_open and is_us_open:
            # 양쪽 다 개장
            cal_title = f"투자일지{session_tag}📈 KOSPI {indices.get('KOSPI',{}).get('rate',0):+.2f}%"
        elif is_kr_open:
            # 한국만 개장
            cal_title = f"투자일지{session_tag}(미장 휴장) 📈 KOSPI {indices.get('KOSPI',{}).get('rate',0):+.2f}%"
        elif is_us_open:
            # 미국만 개장
            cal_title = f"투자일지{session_tag}(국장 휴장) 📈 S&P500 {indices.get('S&P500',{}).get('rate',0):+.2f}%"
        else:
            # 양쪽 다 휴장
            cal_title = f"투자일지{session_tag}📅 주식 시장 휴장"
        
        # 캘린더 본문 생성
        cal_desc_parts = []
        
        # 휴장 안내
        if not is_kr_open and not is_us_open:
            cal_desc_parts.append("## 🚫 주식 시장 휴장")
            cal_desc_parts.append("한국 및 미국 주식 시장은 휴장입니다.")
            cal_desc_parts.append("")
        elif not is_kr_open:
            cal_desc_parts.append("## 🚫 한국 시장 휴장")
            cal_desc_parts.append("한국 주식 시장은 휴장입니다.")
            cal_desc_parts.append("")
        elif not is_us_open:
            cal_desc_parts.append("## 🚫 미국 시장 휴장")
            cal_desc_parts.append("미국 주식 시장은 휴장입니다.")
            cal_desc_parts.append("")
        
        # 관심종목
        cal_desc_parts.append("## ⭐ 관심종목 브리핑")
        cal_desc_parts.append(watch_summary if watch_summary else "등록된 관심종목이 없습니다.")
        cal_desc_parts.append("")
        
        # 시장 지표 (개장한 시장만 표시)
        if is_kr_open or is_us_open:
            cal_desc_parts.append("## 📈 핵심 시장 지표")
            if is_kr_open:
                cal_desc_parts.append(f"- 국장: KOSPI {indices.get('KOSPI',{}).get('price',0):,.1f} ({indices.get('KOSPI',{}).get('rate',0):+.2f}%) / KOSDAQ {indices.get('KOSDAQ',{}).get('price',0):,.1f} ({indices.get('KOSDAQ',{}).get('rate',0):+.2f}%)")
            if is_us_open:
                cal_desc_parts.append(f"- 미장: S&P500 {indices.get('S&P500',{}).get('price',0):,.1f} ({indices.get('S&P500',{}).get('rate',0):+.2f}%) / NASDAQ {indices.get('NASDAQ',{}).get('price',0):,.1f} ({indices.get('NASDAQ',{}).get('rate',0):+.2f}%)")
            cal_desc_parts.append(f"- 환율: USD/KRW {indices.get('USD/KRW',{}).get('price',0):,.1f} (전일대비 {indices.get('USD/KRW',{}).get('change',0):+.1f}원)")
            cal_desc_parts.append("")
        
        
        # 시장 상세 리포트 (주요종목 + 주의종목 통합)
        if unusual_summary != "N/A":
            cal_desc_parts.append("## 📊 시장 상세 리포트 (주의종목)")
            cal_desc_parts.append(row_data[3]) # 시트에 이미 병합된 '누적' 데이터를 사용
            cal_desc_parts.append("")
        
        # 링크
        cal_desc_parts.append("## 🔗 상세 내용 보기")
        cal_desc_parts.append(f"[구글 시트 바로가기](https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID})")
        
        cal_desc = "\n".join(cal_desc_parts)
        self.create_calendar_event(cal_title, cal_desc)
        print(f"모든 작업이 {self.target_date} 기준으로 완료되었습니다.")

    def create_calendar_event(self, title, description):
        # 마스킹 방지 및 오타 확인을 위한 상세 출력
        # id_display = "|".join(list(CALENDAR_ID))
        # print(f"DEBUG: CALENDAR_ID Raw Check: [{id_display}] (Length: {len(CALENDAR_ID)})")
        
        # print("\n--- [Calendar Access Diagnostic] ---")
        # try:
        #     # 1. 접근 가능한 캘린더 목록 출력
        #     cal_list = self.calendar_service.calendarList().list().execute()
        #     items = cal_list.get('items', [])
        #     accessible_ids = [item.get('id') for item in items]
        #     print(f"1. Accessible calendars: {accessible_ids}")
        # 
        #     # 1-1. 서비스 계정 본인의 'primary' 캘린더 정보 조회 테스트 (API 활성화 확인용)
        #     try:
        #         self.calendar_service.calendars().get(calendarId='primary').execute()
        #         print("2. SA's primary calendar access: SUCCESS (API is ENABLED)")
        #     except Exception as sa_e:
        #         print(f"2. SA's primary calendar access: FAILED ({str(sa_e)}) -> API might be DISABLED in GCP console.")
        # 
        #     # 2. CALENDAR_ID가 목록에 있는지 확인
        #     if CALENDAR_ID in accessible_ids:
        #         print(f"3. CALENDAR_ID '{CALENDAR_ID}' is in service account's list: YES")
        #     else:
        #         print(f"3. CALENDAR_ID '{CALENDAR_ID}' is NOT in list. Direct access test needed.")
        # 
        #     # 3. 직접 메타데이터 조회 (접근 가능 여부 최종 확인)
        #     print(f"4. Testing direct access to: {CALENDAR_ID}")
        #     try:
        #         cal_info = self.calendar_service.calendars().get(calendarId=CALENDAR_ID).execute()
        #         print(f"5. CALENDAR_ID '{CALENDAR_ID}' accessible: YES (Summary: {cal_info.get('summary')})")
        #     except Exception as get_e:
        #         print(f"5. Direct access to '{CALENDAR_ID}' FAILED: {str(get_e)}")
        #         
        #         # 4. 강제 구독 시도 (CalendarList.insert)
        #         print(f"6. Attempting to 'subscribe' (insert) calendar '{CALENDAR_ID}' to SA's list...")
        #         try:
        #             self.calendar_service.calendarList().insert(body={'id': CALENDAR_ID}).execute()
        #             print("7. Calendar subscription: SUCCESS! (Now it should be visible)")
        #         except Exception as ins_e:
        #             print(f"7. Calendar subscription FAILED: {str(ins_e)}")
        #             if "forbidden" in str(ins_e).lower():
        #                 print("HINT: This is a PERMISSION issue. Likely your domain admin (Workspace) blocks external sharing.")
        #             elif "notFound" in str(ins_e).lower():
        #                 print("HINT: This is an ID/Typo issue. The calendar ID doesn't exist.")
        # 
        # except Exception as e:
        #     print(f"DIAGNOSTIC_ERROR: Unexpected error during diagnostic: {str(e)}")
        # print("------------------------------------\n")
        # 
        # if CALENDAR_ID == 'primary':
        #     print("WARNING: CALENDAR_ID is set to 'primary'. This points to the Service Account's own calendar.")

        # 해당 날짜의 기존 이벤트 검색 및 삭제 (중복 방지)
        # "투자일지"가 포함된 이벤트를 모두 삭제 (하루에 하나만 유지)
        search_start = self.target_date.isoformat() + "T00:00:00Z"
        search_end = (self.target_date + datetime.timedelta(days=1)).isoformat() + "T00:00:00Z"
        
        try:
            print(f">>> 기존 투자일지 확인 중: {self.target_date.isoformat()}")
            events_result = self.calendar_service.events().list(
                calendarId=CALENDAR_ID, 
                timeMin=search_start, 
                timeMax=search_end,
                singleEvents=True, 
                orderBy='startTime'
            ).execute()
            events = events_result.get('items', [])
            
            deleted_count = 0
            for ev in events:
                ev_summary = ev.get('summary', '')
                ev_date = ev.get('start', {}).get('date')
                
                # 같은 날짜의 "투자일지" 이벤트 삭제
                if ev_date == self.target_date.isoformat() and "투자일지" in ev_summary:
                    try:
                        self.calendar_service.events().delete(calendarId=CALENDAR_ID, eventId=ev['id']).execute()
                        print(f">>> 기존 이벤트 삭제: {ev_summary}")
                        deleted_count += 1
                    except Exception as del_e:
                        print(f"Warning: 이벤트 삭제 실패: {del_e}")
            
            if deleted_count > 0:
                print(f"✅ 기존 이벤트 {deleted_count}개 삭제 완료")
            else:
                print(f"ℹ️ 삭제할 기존 이벤트 없음")
                
        except Exception as e:
            print(f"Warning: 기존 이벤트 검색 실패: {e}")

        # 새 이벤트 생성 준비
        try:
            next_day = (self.target_date + datetime.timedelta(days=1)).isoformat()
            
            event = {
                'summary': title,
                'description': description,
                'start': {'date': self.target_date.isoformat(), 'timeZone': 'Asia/Seoul'},
                'end': {'date': next_day, 'timeZone': 'Asia/Seoul'},
            }
            print(f">>> 새 이벤트 생성 중: {title}")
            res = self.calendar_service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
            print(f"✅ 캘린더 이벤트 생성 완료! Link: {res.get('htmlLink')}")
            
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
    parser.add_argument('target_date', nargs='?', default=None)
    parser.add_argument('--mode', choices=['MORNING', 'MIDDAY', 'CLOSE', 'EVENING', 'AUTO'], default='AUTO')
    args = parser.parse_args()
    
    if not SPREADSHEET_ID:
        print("ERROR: SPREADSHEET_ID is missing.")
        sys.exit(1)
        
    updater = StockDataUpdater(target_date=args.target_date)
    updater.process_and_report(mode=args.mode, manual_date=(args.target_date is not None))
