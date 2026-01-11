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
VERSION = "v2.6.7_20260111"

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
    
    def _normalize_ticker(self, ticker, asset_type):
        """티커 형식 표준화 (KR: 6자리 숫자, US: 대문자 등)"""
        t = str(ticker).strip()
        if asset_type == 'KR' and t.isdigit():
            return t.zfill(6)
        return t.upper() if asset_type == 'US' else t

    def get_market_indices(self):
        """시장 지수 및 환율 수집 (시트 설정 기반 또는 기본값)"""
        # 1. 시트에서 Category='Index'인 항목 가져오기 시도
        index_map = {}
        try:
            ws = self.sh.worksheet('관심종목_관리')
            all_records = ws.get_all_records()
            for r in all_records:
                # Use robust key matching for localization
                category = str(r.get('카테고리', r.get('Category', ''))).strip().lower()
                if category in ['index', '지수', 'exchange', '환율']:
                    ticker = str(r.get('티커', r.get('Ticker', ''))).strip()
                    name = str(r.get('종목명', r.get('Name', ''))).strip()
                    if ticker and name:
                        index_map[ticker] = {'name': name, 'category': category}
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
        
        for ticker, info in index_map.items():
            name = info['name']
            cat = info['category']
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
                    'date': curr.name.date().isoformat(),
                    'category': cat
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
        
        # 주요 종목 데이터 섹션 (v2.6.7: 3대 체제 및 국가별 세분화)
        # 1. 주요종목 ([주요종목:한국], [주요종목:미국], [주요종목:코인])
        major_data = [d for d in data if str(d.get('Category', d.get('카테고리', ''))).lower() in ['major', '주요종목']]
        if major_data:
            rows.append(['=== 📌 [주요종목] ===', '', '', '', '', '', ''])
            for a_code, a_name in [('KR', '한국'), ('US', '미국'), ('Coin', '코인')]:
                subset = [d for d in major_data if d.get('Asset') == a_code]
                if subset:
                    rows.append([f'[{a_name}]', '티커', '종목명', '현재가', '변동률', '거래량', '시총'])
                    for d in subset:
                        rows.append([
                            '', d['Ticker'], d['Name'], 
                            d['FormattedPrice'], f"{d['ChangeRate']:+.2f}%", 
                            self._format_large_number(d['Volume']), 
                            self._format_large_number(d['MarketCap'])
                        ])
            rows.append([])

        # 2. 코인 전용 섹션 (Major에 포함되지 않은 일반 코인들)
        crypto_data = [d for d in data if str(d.get('Category', d.get('카테고리', ''))).lower() in ['crypto', '코인'] and d not in major_data]
        if crypto_data:
            rows.append(['=== 🪙 [코인] ===', '', '', '', '', '', ''])
            rows.append(['자산', '티커', '종목명', '현재가', '변동률', '거래량', '시총'])
            for d in crypto_data:
                rows.append([
                    d['Asset'], d['Ticker'], d['Name'], 
                    d['FormattedPrice'], f"{d['ChangeRate']:+.2f}%", 
                    self._format_large_number(d['Volume']), 
                    self._format_large_number(d['MarketCap'])
                ])
            rows.append([])

        # 3. 관심종목 ([관심종목:한국], [관심종목:미국])
        watch_data_all = [d for d in data if d not in major_data and d not in crypto_data]
        if watch_data_all:
            rows.append(['=== ⭐ [관심종목] ===', '', '', '', '', '', ''])
            for a_code, a_name in [('KR', '한국'), ('US', '미국')]:
                subset = [d for d in watch_data_all if d.get('Asset') == a_code]
                if subset:
                    rows.append([f'[{a_name}]', '티커', '종목명', '현재가', '변동률', '거래량', '시총'])
                    for d in subset:
                        rows.append([
                            '', d['Ticker'], d['Name'], 
                            d['FormattedPrice'], f"{d['ChangeRate']:+.2f}%", 
                            self._format_large_number(d['Volume']), 
                            self._format_large_number(d['MarketCap'])
                        ])
            rows.append([])
        
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
                mgmt_ws.append_row(['구분', '티커', '종목명', '카테고리', '테마', '메모', '알림가', '시스템추천', '전문가의견', '정보링크'])
            
            current_mgmt = mgmt_ws.get_all_records()
            # 중복 체크 고도화: (국가, 티커) 튜플로 관리 (v2.6.0)
            mgmt_keys = set()
            for r in current_mgmt:
                a_type = r.get('구분', r.get('Asset', 'US'))
                t_raw = r.get('티커', r.get('Ticker', ''))
                t_norm = self._normalize_ticker(t_raw, a_type)
                mgmt_keys.add((a_type, t_norm))
            
            # 3. 요청 처리 및 동기화 준비
            final_watchlist = []
            status_updates = [] # '검색결과' 컬럼 업데이트용
            name_updates = []   # '종목명' 컬럼 업데이트용
            rows_to_add = []    # '관심종목_관리'에 추가할 행들
            keys_to_remove = set() # 삭제할 (국가, 티커) 세트
            processed_keys = set() # 현재 요청에서 이미 처리된 종목들 (중복 요청 방지)
            
            # TickerFound(검색결과) 및 종목명 필드 인덱스 찾기
            headers_rq = rq_ws.row_values(1)
            found_col_idx = 6 # 기본값
            name_col_idx = 2  # 기본값
            if '검색결과' in headers_rq: found_col_idx = headers_rq.index('검색결과') + 1
            elif 'TickerFound' in headers_rq: found_col_idx = headers_rq.index('TickerFound') + 1
            
            if '종목명' in headers_rq: name_col_idx = headers_rq.index('종목명') + 1
            elif 'Name' in headers_rq: name_col_idx = headers_rq.index('Name') + 1

            for i, req in enumerate(requests):
                ticker_raw = str(get_val(req, '티커', 'Ticker')).strip()
                name_req = str(get_val(req, '종목명', 'Name')).strip()
                enabled = str(get_val(req, '사용여부', 'RequestEnabled')).upper() == 'TRUE'
                category = str(get_val(req, '카테고리', 'Category')).strip()
                memo = get_val(req, '메모', 'Memo')
                
                if not (ticker_raw or name_req):
                    status_updates.append("")
                    name_updates.append("")
                    continue
                
                # 임시 asset_type 결정 (티커 형식으로 1차 판단)
                tmp_asset = 'US'
                if ticker_raw.isdigit(): tmp_asset = 'KR'
                elif '-' in ticker_raw: tmp_asset = 'Coin'
                
                ticker = self._normalize_ticker(ticker_raw, tmp_asset)

                found_ticker = None
                asset_type = tmp_asset
                
                # Ticker/Name으로 종목 찾기
                if ticker:
                    try:
                        if asset_type == 'KR':
                            if ticker in self.kr_name_map: found_ticker = ticker
                        elif asset_type == 'Coin':
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
                status_updates.append(found_status)
                
                # 종목명 자동 채우기 준비
                current_name = name_req
                if found_ticker and not current_name:
                    current_name = self.kr_name_map.get(found_ticker, found_ticker)
                name_updates.append(current_name)
                
                if found_ticker:
                    key = (asset_type, found_ticker)
                    
                    if enabled and found_ticker:
                        if key in processed_keys:
                            print(f"Skipping duplicate request: {key}")
                            continue
                        
                        processed_keys.add(key)
                        
                        # 현재 관리 시트에서 기존 정보 유지
                        existing_item = next((r for r in current_mgmt if (r.get('구분', r.get('Asset', '')) == asset_type and self._normalize_ticker(r.get('티커', r.get('Ticker', '')), asset_type) == found_ticker)), {})
                        
                        final_watchlist.append({
                            'Ticker': found_ticker,
                            'Asset': asset_type,
                            'Memo': memo,
                            'Category': category,
                            'AlertPrice': existing_item.get('알림가', existing_item.get('Alert_Price', '')),
                            'ExpertOpinion': existing_item.get('전문가의견', existing_item.get('Expert_Opinion', ''))
                        })
                        
                        if key not in mgmt_keys:
                            name_display = self.kr_name_map.get(found_ticker, current_name or found_ticker)
                            rows_to_add.append([
                                asset_type, found_ticker, name_display, category, "", memo, "", "", "", ""
                            ])
                            mgmt_keys.add(key)
                        else:
                            # 기존 종목인 경우 카테고리 등 기본 정보 업데이트 (v2.6.6)
                            mgmt_header = mgmt_ws.row_values(1)
                            cat_col = mgmt_header.index('카테고리') + 1 if '카테고리' in mgmt_header else 4
                            
                            current_cat = existing_item.get('카테고리', existing_item.get('Category', ''))
                            if current_cat != category:
                                # 행 번호 찾기 (all_cells 기준)
                                mgmt_all_values = mgmt_ws.get_all_values()
                                for idx, row in enumerate(mgmt_all_values[1:], start=2):
                                    if row[0] == asset_type and self._normalize_ticker(row[1], asset_type) == found_ticker:
                                        mgmt_ws.update_cell(idx, cat_col, category)
                                        print(f"Updated category for {found_ticker}: {category}")
                                        break
                    else:
                        # 비활성화 이거나 검색결과가 FALSE인 경우 삭제 (v2.6.6)
                        if key in mgmt_keys:
                            keys_to_remove.add(key)
                            if key in mgmt_keys: mgmt_keys.remove(key)

            # 4. Batch Updates 실행
            
            # 4-1. 요청 시트의 검색결과 및 종목명 일괄 업데이트
            if status_updates:
                range_found = f"{gspread.utils.rowcol_to_a1(2, found_col_idx)}:{gspread.utils.rowcol_to_a1(len(requests)+1, found_col_idx)}"
                rq_ws.update(values=[[s] for s in status_updates], range_name=range_found)
                
                range_name = f"{gspread.utils.rowcol_to_a1(2, name_col_idx)}:{gspread.utils.rowcol_to_a1(len(requests)+1, name_col_idx)}"
                rq_ws.update(values=[[n] for n in name_updates], range_name=range_name)
                
                print(f"Batch updated rq_ws: {len(status_updates)} items.")

            # 4-2. 관리 시트에 새 종목 일괄 추가
            if rows_to_add:
                mgmt_ws.append_rows(rows_to_add)
                for r in rows_to_add: print(f"Added to management: {r[1]}")

            # 4-3. 관리 시트에서 비활성 종목 일괄 삭제
            if keys_to_remove:
                headers_mgmt = mgmt_ws.row_values(1)
                asset_col = (headers_mgmt.index('구분') + 1) if '구분' in headers_mgmt else 1
                ticker_col = (headers_mgmt.index('티커') + 1) if '티커' in headers_mgmt else 2
                
                all_cells = mgmt_ws.get_all_values()
                rows_to_del = []
                for i, row in enumerate(all_cells[1:], start=2):
                    r_asset = row[asset_col-1]
                    r_ticker = self._normalize_ticker(row[ticker_col-1], r_asset)
                    if (r_asset, r_ticker) in keys_to_remove:
                        rows_to_del.append(i)
                
                for r_idx in sorted(rows_to_del, reverse=True):
                    mgmt_ws.delete_rows(r_idx)
                    print(f"Removed row {r_idx} from management.")

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
        headers = ['날짜', '시장 요약', '관심종목 현황', '주의종목', '갱신날짜']
        
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
            ws.update(values=[headers], range_name='A1')
            
            # 모든 컬럼 상단 정렬 적용
            try:
                fmt_top = CellFormat(verticalAlignment='TOP')
                format_cell_range(ws, 'A:E', fmt_top)  # 모든 컬럼 상단 정렬
                print(f"✅ 새 월별 시트 생성 및 서식 적용: {tab_name}")
            except Exception as e:
                print(f"✅ 새 월별 시트 생성: {tab_name} (서식 적용 실패: {e})")
            
            return ws

    def process_and_report(self, mode="AUTO", manual_date=False):
        print(f"--- Running Updater (v{VERSION}, Mode: {mode}, Target: {self.target_date}) ---")
        
        print("1. 동기화 중: 관심종목_요청 내역 반영...")
        watchlist_raw = self.get_watchlist()
        
        print("2. 수집 중: 시장 지표 및 실제 거래일 확인...")
        indices = self.get_market_indices()
        
        # [v2.6.4] 기록 기준 날짜 (target_date) 보존 정책
        # 시장 데이터가 없거나 과거 날짜라 하더라도, 기록은 '오늘 실행한 날짜' 줄에 남겨야 함.
        # 따라서 indices에서 얻은 날짜로 self.target_date를 덮어쓰는 로직을 제거함.
        
        market_date_info = ""
        if indices:
            dates = [v.get('date') for v in indices.values() if v.get('date')]
            if dates:
                actual_market_date = max(dates)
                if actual_market_date != self.target_date.isoformat():
                    market_date_info = f" (최신 시장 데이터: {actual_market_date})"
                    print(f">>> Market Date Detected: {actual_market_date} {market_date_info}")

        print(f">>> Final Logging Date: {self.target_date}{market_date_info}")
        
        # 실제 데이터 존재 여부로 시장 개장 여부 판단 (기록일 기준)
        kospi_date = indices.get('KOSPI', {}).get('date')
        sp500_date = indices.get('S&P500', {}).get('date')
        
        is_kr_open = (kospi_date == self.target_date.isoformat())
        is_us_open = (sp500_date == self.target_date.isoformat())
        
        # [v2.6.7] 과거 날짜 요청 시 최종 마감 모드 강제 및 지수/코인 필수 포함 보장
        if manual_date:
            print(f">>> [Historical Sync] {self.target_date} 데이터의 최종 마감 상태를 기록합니다.")
            # 과거 날짜는 이미 장이 끝났으므로 모든 정보 수집 허용
            is_kr_open = True if kospi_date else is_kr_open
            is_us_open = True if sp500_date else is_us_open
        
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

        print("3. 수집 중: 개인 관심종목 데이터...")
        watch_data = [] # 개인 관심종목
        major_data = [] # 주요 종목 (지수 옆 표시용)
        
        for item in watchlist_raw:
            ticker = str(item.get('Ticker', ''))
            if not ticker: continue
            
            # Use robust key matching
            category = str(item.get('카테고리', item.get('Category', ''))).strip().lower()
            if category in ['index', '지수']: continue # 지수는 이미 수집됨
            
            asset_type = item.get('Asset', 'US')
            
            # 시장 개장 상태와 상관없이 '오늘' 탭을 위해 데이터를 수집합니다 (휴장일은 마지막 거래일 종가 표시)
            if asset_type == 'KR' and not is_kr_open:
                # 휴장일이라도 데이터는 가져오되, 로그로 알림
                pass
            if asset_type == 'US' and not is_us_open:
                pass
            
            res = self.get_stock_data([ticker], asset_type)
            if res:
                # 시트의 사용자 메모 및 의견 병합
                res[0]['Memo'] = item.get('메모', item.get('Memo', ''))
                res[0]['ExpertOpinion_User'] = item.get('전문가의견', item.get('Expert_Opinion', ''))
                res[0]['Category'] = item.get('카테고리', item.get('Category', 'Watchlist'))
                
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
                    # 필드 맵핑 (구분, 티커, 종목명, 카테고리, 테마, 메모, 알림가, 시스템추천, 전문가의견, 정보링크)
                    # 영어/한글 혼용 대응을 위해 인덱스 기반 업데이트 권장
                    headers = mgmt_ws.row_values(1)
                    try:
                        if '테마' in headers: mgmt_ws.update_cell(row_idx, headers.index('테마') + 1, info.get('Theme', '-'))
                        if '시스템추천' in headers: mgmt_ws.update_cell(row_idx, headers.index('시스템추천') + 1, info.get('Recommendation', '-'))
                        if '정보링크' in headers: mgmt_ws.update_cell(row_idx, headers.index('정보링크') + 1, info.get('InfoLink', ''))
                        if '종목명' in headers: mgmt_ws.update_cell(row_idx, headers.index('종목명') + 1, info.get('Name', ''))
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
        kr_indices = [f"{k} {v['rate']:+.2f}%" for k, v in indices.items() if v.get('category') in ['index', '지수'] and k in ['KOSPI', 'KOSDAQ']]
        us_indices = [f"{k} {v['rate']:+.2f}%" for k, v in indices.items() if v.get('category') in ['index', '지수'] and k in ['S&P500', 'NASDAQ', 'Dow Jones']]
        ex_indices = [f"{k} {v['price']:,.1f} ({v['change']:+.1f})" for k, v in indices.items() if v.get('category') in ['exchange', '환율']]
        other_indices = [f"{k} {v['price']:,.1f} ({v['change']:+.1f})" for k, v in indices.items() if v.get('category') not in ['index', '지수', 'exchange', '환율']]

        if kr_indices: market_summary_lines.append(f"KR 지수: {', '.join(kr_indices)}")
        if us_indices: market_summary_lines.append(f"US 지수: {', '.join(us_indices)}")
        if ex_indices: market_summary_lines.append(f"환율: {', '.join(ex_indices)}")
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
        
        # [v2.6.7] 리포트 3대 체제 ([주요종목], [관심종목], [특이종목]) 재편
        print("4.5. 리포트 본문 생성 중 (3대 체제)...")
        report_lines = []
        
        # 1. [주요종목] 섹션
        major_list = [d for d in market_all if str(d.get('Category', '')).lower() == 'major']
        if major_list:
            report_lines.append("=== [주요종목] ===")
            for a_code, a_name in [('KR', '한국'), ('US', '미국'), ('Coin', '코인')]:
                subset = [d for d in major_list if d.get('Asset') == a_code and d.get('Price', 0) > 0]
                if subset:
                    report_lines.append(f"[주요종목:{a_name}]")
                    for d in subset:
                        report_lines.append(f"[{d['Ticker']}] {d['Name']} ({d['FormattedPrice']} / {d['ChangeRate']:+.2f}%)")
                    report_lines.append("")

        # 2. [관심종목] 섹션
        watch_list = [d for d in market_all if str(d.get('Category', '')).lower() not in ['major', 'crypto', 'index', 'exchange']]
        if watch_list:
            report_lines.append("=== [관심종목] ===")
            for a_code, a_name in [('KR', '한국'), ('US', '미국')]:
                subset = [d for d in watch_list if d.get('Asset') == a_code and d.get('Price', 0) > 0]
                if subset:
                    report_lines.append(f"[관심종목:{a_name}]")
                    for d in subset:
                        rec_part = f" [{d['FinalRecommendation']}]" if d['FinalRecommendation'] != "-" else ""
                        report_lines.append(f"[{d['Ticker']}] {d['Name']} ({d['FormattedPrice']} / {d['ChangeRate']:+.2f}%){rec_part}")
                    report_lines.append("")

        # 3. [특이종목] 섹션 (Scraped)
        if unusual:
            report_lines.append("=== [특이종목] ===")
            for a_code, a_name in [('KR', '한국'), ('US', '미국')]:
                subset = [d for d in unusual if d.get('Asset') == a_code and d.get('Price', 0) > 0]
                if subset:
                    report_lines.append(f"[특이종목:네이버/FDR수집:{a_name}]")
                    for d in subset[:10]:
                        source_tag = f"[{d.get('Source', '주의')}]" if d.get('Source') != 'Internal' else "[급변]"
                        report_lines.append(f"{source_tag} {d['Name']} ({d['FormattedPrice']} / {d['ChangeRate']:+.2f}%)")
                    report_lines.append("")

        detailed_market_info = "\n".join(report_lines) if report_lines else "N/A"

        print("5. 기록 중: 월별 일지 (중복 체크 포함)...")
        ws_monthly = self.get_monthly_worksheet()
        all_dates = ws_monthly.col_values(1)
        target_iso = self.target_date.isoformat()

        row_data = [
            target_iso,
            market_summary,
            watch_summary if watch_summary else "N/A",
            detailed_market_info,
            datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        ]


        if target_iso in all_dates:
            row_idx = all_dates.index(target_iso) + 1
            if manual_date:
                # [v2.6.7] 과거 날짜 요청 시 덮어쓰기 (최종본 정책)
                ws_monthly.update(values=[row_data], range_name=f'A{row_idx}:E{row_idx}')
                print(f"Overwritten (Final State) for past date: {target_iso}.")
            else:
                existing_row = ws_monthly.row_values(row_idx)
                
                # 데이터 병합 로직 (기존 데이터가 "N/A"가 아니면 유지하고 현재 데이터와 병합)
                # Market Summary 병합
                new_market = row_data[1]
                old_market = existing_row[1] if len(existing_row) > 1 else ""
                merged_market = self._merge_report_sections(old_market, new_market, ["KR 지수:", "US 지수:", "환율:"])
                
                # Watchlist Status 병합
                new_watch = row_data[2]
                old_watch = existing_row[2] if len(existing_row) > 2 else ""
                merged_watch = self._merge_report_sections(old_watch, new_watch, ["KR:", "US:", "Coin:"])
                
                # Detailed Info 병합 (마커 기반)
                new_detailed = row_data[3]
                old_detailed = existing_row[3] if len(existing_row) > 3 else ""
                markers = [
                    "[주요종목:한국]", "[주요종목:미국]", "[주요종목:코인]",
                    "[관심종목:한국]", "[관심종목:미국]",
                    "[특이종목:네이버/FDR수집:한국]", "[특이종목:네이버/FDR수집:미국]"
                ]
                merged_detailed = self._merge_report_sections(old_detailed, new_detailed, markers)

                row_data[1] = merged_market
                row_data[2] = merged_watch
                row_data[3] = merged_detailed
                
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
