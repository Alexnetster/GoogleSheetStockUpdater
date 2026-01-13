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
VERSION = "v2.7.0_20260111"

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
        
        # [v2.6.9] Local Verification Hook (Developer Only)
        self.debug_mode = False 

    def _log_payload(self, target, data):
        """로컬 검증을 위한 내부 훅 (tools/verify_sync.py에서 오버라이드하여 사용)"""
        pass

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
        ws_name = '종목_관리'
        # [v2.7.0] Migration: Check for old sheet name and rename if necessary
        try:
            self.sh.worksheet(ws_name)
        except gspread.exceptions.WorksheetNotFound:
            try:
                old_ws = self.sh.worksheet('관심종목_관리')
                old_ws.update_title(ws_name)
                print(f"Migrated sheet '{old_ws.title}' to '{ws_name}'")
            except gspread.exceptions.WorksheetNotFound:
                pass # Will be handled/created later if needed

        try:
            ws = self.sh.worksheet(ws_name)
            all_records = ws.get_all_records()
            for r in all_records:
                # Use robust key matching for localization
                category = str(r.get('카테고리', r.get('Category', ''))).strip()
                # Normalize to Korean for consistent checking
                if category in ['index', 'Index', '지수']: category = '지수'
                if category in ['exchange', 'Exchange', '환율']: category = '환율'

                if category in ['지수', '환율']:
                    ticker = str(r.get('티커', r.get('Ticker', ''))).strip()
                    name = str(r.get('종목명', r.get('Name', ''))).strip()
                    if ticker and name:
                        index_map[ticker] = {'name': name, 'category': category}
        except:
            pass

        if not index_map:
            print(f"⚠️  [필독] 시트('{ws_name}')에서 '지수' 또는 '환율' 카테고리를 찾을 수 없습니다.")
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

    def update_today_data(self, data, indices=None, is_kr_open=True, is_us_open=True, mode="AUTO", cautionary_list=[]):
        """'오늘' 시트 갱신: Dashboard Mode (항상 덮어쓰기)"""
        # [v2.6.9] 데이터 검증용 로그
        self._log_payload("today_raw", {"data": data, "indices": indices})
        
        sheet_name = '오늘'
        try:
            ws = self.sh.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            ws = self.sh.add_worksheet(title=sheet_name, rows=200, cols=10)
        
        # 1. Dashboard Mode: Always Clear and Overwrite
        if self.debug_mode:
             print("   [DEBUG] Dry-run: Skipping 'Today' sheet clear.")
             start_row = 1
        else:
             try:
                 ws.clear()
                 print("   [Info] 'Today' sheet cleared for Dashboard update.")
             except: pass
             start_row = 1

        rows = []
        
        # 세션명 결정
        target_date_str = self.target_date.strftime('%Y-%m-%d')
        now = datetime.datetime.now()
        now_time_str = now.strftime('%H:%M:%S')
        weekdays = ["월", "화", "수", "목", "금", "토", "일"]
        full_date_display = f"{target_date_str} ({weekdays[self.target_date.weekday()]})"
        
        session_name = mode
        if mode == "AUTO":
            h = now.hour
            if 7 <= h < 11: session_name = "MORNING"
            elif 11 <= h < 15: session_name = "MIDDAY"
            elif 15 <= h < 19: session_name = "CLOSE"
            else: session_name = "EVENING"
        
        session_kr = {"MORNING": "모닝 브리핑", "MIDDAY": "미드데이 브리핑", "CLOSE": "장마감 리뷰", "EVENING": "이브닝 브리핑"}.get(session_name, "수시 업데이트")
        
        # 시장 상태 요약
        kr_status = "개장" if is_kr_open else "휴장"
        us_status = "개장" if is_us_open else "휴장"
        
        rows.append([f"=== 🕒 {session_kr} ({now_time_str}) ===", "", "", "", "", "", ""])
        rows.append([f"기준 일자: {full_date_display} [국장 {kr_status}][미장 {us_status}]", "", "", "", "", "", ""])
        rows.append([])

        # 2. 시장 지표 (필터링 적용)
        if indices:
            rows.append(['=== 📊 지수 및 환율 ===', '', '', '', '', '', ''])
            rows.append(['번호', '국가', '거래소', '지수', '변동률', '', ''])
            
            idx_num = 1
            for k, v in indices.items():
                is_kr_idx = k in ['KOSPI', 'KOSDAQ']
                is_us_idx = k in ['S&P500', 'NASDAQ', 'Dow Jones']
                is_exchange = (v.get('category') == 'exchange' or k == 'USD/KRW')
                
                # [v2.7.0] 휴장이라도 수집된 지수가 있으면 표시 (필터링 제거)
                # if is_kr_idx and not is_kr_open: continue
                # if is_us_idx and not is_us_open: continue
                
                if is_kr_idx: country, exchange = "한국", k
                elif is_us_idx: country, exchange = "미국", k
                elif is_exchange: country, exchange = "환율", k
                else: country, exchange = "-", k
                
                rows.append([str(idx_num), country, exchange, f"{v['price']:,.1f}", f"{v['rate']:+.2f}%", '', ''])
                idx_num += 1
            rows.append([])

        # 3. 종목 데이터 (필터링 적용)
        # 카테고리별 출력 (주요종목 -> 가상화폐 -> 관심종목)
        # [v2.7.0] Korean Category Mapping
        major_data = [d for d in data if str(d.get('Category', '')).strip() in ['Major', '주요', '주요종목']]
        crypto_data = [d for d in data if str(d.get('Category', '')).strip() in ['Crypto', 'Coin', '코인', '가상화폐'] and d not in major_data]
        watch_data = [d for d in data if d not in major_data and d not in crypto_data]

        # [v2.7.0] 국가별 섹션 분리
        major_kr = [d for d in major_data if d.get('Asset') == 'KR']
        major_us = [d for d in major_data if d.get('Asset') == 'US']
        major_etc = [d for d in major_data if d.get('Asset') not in ['KR', 'US']] # 혹시 모를 기타

        watch_kr = [d for d in watch_data if d.get('Asset') == 'KR']
        watch_us = [d for d in watch_data if d.get('Asset') == 'US']
        watch_etc = [d for d in watch_data if d.get('Asset') not in ['KR', 'US']]

        sections = [
            ('📌 [주요종목: 한국]', major_kr),
            ('📌 [주요종목: 미국]', major_us + major_etc), # 기타는 미국 쪽에 병합 표시
            ('⭐ [관심종목: 한국]', watch_kr),
            ('⭐ [관심종목: 미국]', watch_us + watch_etc),
            ('🪙 [코인]', crypto_data),
            ('🚨 [주의종목]', cautionary_list)
        ]

        for sec_name, sec_list in sections:
            # 시장 상태에 따른 필터링
            filtered = []
            for d in sec_list:
                asset = d.get('Asset', 'US')
                # [v2.7.0] 휴장이라도 수집된 종목은 표시 (필터링 제거)
                # if asset == 'KR' and not is_kr_open: continue
                # if asset == 'US' and not is_us_open: continue
                # Coin은 항상 포함
                filtered.append(d)
            
            if not filtered: continue
            
            rows.append([f'=== {sec_name} ===', '', '', '', '', '', ''])
            rows.append(['자산', '티커', '종목명', '현재가', '변동률', '거래량', '시총'])
            for d in filtered:
                rows.append([
                    d.get('Asset', '-'), d.get('Ticker', '-'), d.get('Name', '-'), 
                    d.get('FormattedPrice', '-'), f"{d.get('ChangeRate', 0):+.2f}%", 
                    self._format_large_number(d.get('Volume', 0)), 
                    self._format_large_number(d.get('MarketCap', 0))
                ])
            rows.append([])

        # 데이터 쓰기
        range_name = f'A{start_row}'
        # [v2.6.9] 최종 출력 데이터 로그
        self._log_payload("today_rows", rows)
        
        if not self.debug_mode:
            ws.update(values=rows, range_name=range_name)
        
        # 서식 지정 (간략화)
        # 서식 지정 (Explicit Formatting)
        try:
            # 1. 포맷 정의
            fmt_left = CellFormat(horizontalAlignment='LEFT', textFormat=TextFormat(bold=False))
            fmt_right = CellFormat(horizontalAlignment='RIGHT', textFormat=TextFormat(bold=False))
            fmt_header = CellFormat(horizontalAlignment='CENTER', textFormat=TextFormat(bold=True))
            
            # 2. 전체 데이터 영역 기본 정렬 (데이터 행 기준)
            # A~C열: 좌측 정렬 (자산, 티커, 종목명)
            format_cell_range(ws, f'A{start_row}:C{start_row+len(rows)}', fmt_left)
            # D~G열: 우측 정렬 (수치 데이터)
            format_cell_range(ws, f'D{start_row}:G{start_row+len(rows)}', fmt_right)
            
            # 3. 헤더 행(섹션/컬럼) 강조
            header_keywords = ["자산", "티커", "종목명", "번호", "국가"]
            for i, r in enumerate(rows):
                if not r: continue
                # 섹션 헤더 (===) 또는 컬럼 헤더 (자산, 번호 등)
                str_val = str(r[0])
                if str_val.startswith("===") or str_val in header_keywords:
                    format_cell_range(ws, f'A{start_row+i}:G{start_row+i}', fmt_header)
            
            print(f"✅ 오늘 시트 업데이트 완료 ({session_kr}) - 포맷 적용됨")
        except Exception as e:
            print(f"Warning: 서식 적용 실패: {e}")

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

    def manage_cautionary_buffer(self, new_items):
        """'주의종목_버퍼' 탭을 관리하여 일간 누적 데이터를 반환"""
        sheet_name = '주의종목_버퍼'
        try:
            ws = self.sh.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            if not self.debug_mode:
                ws = self.sh.add_worksheet(title=sheet_name, rows=500, cols=8)
                ws.append_row(['날짜', '자산', '티커', '종목명', '현재가', '변동률', '거래량', '출처'])
            else:
                return new_items

        # 1. 초기화 여부 확인 (날짜 변경 시 Clear)
        target_date_str = self.target_date.strftime('%Y-%m-%d')
        if not self.debug_mode:
            try:
                first_row = ws.row_values(1)
                first_val = ws.acell('A2').value if len(ws.col_values(1)) > 1 else None
                
                # 시트가 비어있거나, 날짜가 다르면 초기화
                if not first_val or first_val != target_date_str:
                    ws.clear()
                    ws.append_row(['날짜', '자산', '티커', '종목명', '현재가', '변동률', '거래량', '출처'])
                    print(f" [Buffer] New day detected. Cleared '{sheet_name}'.")
            except: pass

        # 2. 기존 데이터 로드 (누적용)
        existing_tickers = set()
        current_rows = []
        if not self.debug_mode:
            raw_data = ws.get_all_records()
            for r in raw_data:
                # 시트 헤더가 한글이므로 한글 키 사용
                asset = r.get('자산', r.get('Asset', ''))
                ticker = str(r.get('티커', r.get('Ticker', '')))
                
                # 키: (Asset, Ticker)
                key = (asset, ticker)
                existing_tickers.add(key)
                
                # 내부 로직용 영문 키로 변환하여 리스트에 추가
                std_item = {
                    'Asset': asset,
                    'Ticker': ticker,
                    'Name': r.get('종목명', r.get('Name', '')),
                    'FormattedPrice': r.get('현재가', r.get('Price', '')),
                    'ChangeRate': r.get('변동률', r.get('Change', 0)),
                    'Volume': r.get('거래량', r.get('Volume', 0)),
                    'Source': r.get('출처', r.get('Source', ''))
                }
                # 변동률 등 숫자 변환 필요시 추가 처리 (일단 그대로 사용)
                try:
                    if isinstance(std_item['ChangeRate'], str) and '%' in std_item['ChangeRate']:
                        std_item['ChangeRate'] = float(std_item['ChangeRate'].replace('%', ''))
                except: pass
                
                current_rows.append(std_item) # 로컬 변환용
        
        # 3. 새로운 데이터 병합
        rows_to_add = []
        final_list = list(current_rows) # 기존 데이터로 시작
        
        for item in new_items:
            key = (item.get('Asset'), str(item.get('Ticker')))
            if key not in existing_tickers:
                # 시트 추가용 행
                row = [
                    target_date_str,
                    item.get('Asset', ''),
                    item.get('Ticker', ''),
                    item.get('Name', ''),
                    item.get('FormattedPrice', ''),
                    item.get('ChangeRate', 0),
                    item.get('Volume', 0),
                    item.get('Source', '')
                ]
                rows_to_add.append(row)
                existing_tickers.add(key)
                
                # 반환 리스트에도 포맷 맞춰 추가
                final_list.append(item)
        
        # 4. 시트 업데이트
        if rows_to_add and not self.debug_mode:
            ws.append_rows(rows_to_add)
            
            # 서식 적용 (상단 정렬 & 우측 정렬)
            try:
                # 포맷 정의 (모두 상단 정렬 적용)
                fmt_header = CellFormat(horizontalAlignment='CENTER', verticalAlignment='TOP', textFormat=TextFormat(bold=True))
                fmt_normal = CellFormat(horizontalAlignment='LEFT', verticalAlignment='TOP')
                fmt_number = CellFormat(horizontalAlignment='RIGHT', verticalAlignment='TOP')
                
                last_row = len(final_list) + 1
                range_end = last_row + 10 # 여유분

                if last_row > 1:
                    # 1. 전체 데이터 영역: Top + Left (기본)
                    format_cell_range(ws, f'A2:H{range_end}', fmt_normal)
                    # 2. 숫자 데이터 영역: Top + Right (덮어쓰기)
                    format_cell_range(ws, f'E2:G{range_end}', fmt_number)
                
                # 3. 헤더: Top + Center + Bold
                format_cell_range(ws, 'A1:H1', fmt_header)
            except: pass
            
            print(f" [Buffer] Added {len(rows_to_add)} new items to '{sheet_name}'.")
            
        # [v2.7.0] 버퍼 데이터 검증용 로그
        self._log_payload("cautionary_buffer", final_list)

        return final_list

    def _map_category_to_korean(self, category):
        """[v2.7.0] Map English/Legacy categories to Korean standard terms"""
        c = str(category).strip().lower()
        if c in ['index', '지수']: return '지수'
        if c in ['exchange', '환율']: return '환율'
        if c in ['major', '주요', '주요종목']: return '주요종목'
        if c in ['crypto', 'coin', '코인', '가상화폐']: return '가상화폐'
        if c in ['watchlist', '관심', '관심종목', '']: return '관심종목'
        return category # Fallback

    def get_stock_list(self):
        """[Renamed] 종목_요청 기반으로 종목_관리 동기화 및 데이터 반환"""
        try:
            # 1. 종목_요청 시트 로드 (없으면 생성 / 마이그레이션)
            req_sheet_name = '종목_요청'
            try:
                # Check for migration
                try:
                    self.sh.worksheet(req_sheet_name)
                except gspread.exceptions.WorksheetNotFound:
                    old_ws = self.sh.worksheet('관심종목_요청')
                    old_ws.update_title(req_sheet_name)
                    print(f"Migrated sheet '관심종목_요청' to '{req_sheet_name}'")
            except: pass # '관심종목_요청' didn't exist either

            try:
                rq_ws = self.sh.worksheet(req_sheet_name)
            except gspread.exceptions.WorksheetNotFound:
                if not self.debug_mode:
                    rq_ws = self.sh.add_worksheet(title=req_sheet_name, rows=100, cols=6)
                    rq_ws.append_row(['티커', '종목명', '카테고리', '메모', '사용여부', '검색결과'])
                    print(f"Created '{req_sheet_name}' sheet.")
                else:
                    return [] 
            
            requests = rq_ws.get_all_records()
            # [v2.7.0] 초기 요청 데이터 로그 (검증용)
            self._log_payload("watchlist_request", requests) # Key kept as 'watchlist_request' for legacy compatibility in logs

            
            # 한글/영어 키 모두 대응 (과도기 지원)
            def get_val(r, kor, eng): return r.get(kor, r.get(eng, ''))

            # 2. 종목_관리 시트 로드 (없으면 생성 / 마이그레이션)
            mgmt_sheet_name = '종목_관리'
            try:
                # Check for migration
                try:
                    self.sh.worksheet(mgmt_sheet_name)
                except gspread.exceptions.WorksheetNotFound:
                    old_ws = self.sh.worksheet('관심종목_관리')
                    old_ws.update_title(mgmt_sheet_name)
                    print(f"Migrated sheet '관심종목_관리' to '{mgmt_sheet_name}'")
            except: pass

            try:
                mgmt_ws = self.sh.worksheet(mgmt_sheet_name)
            except gspread.exceptions.WorksheetNotFound:
                if not self.debug_mode:
                    mgmt_ws = self.sh.add_worksheet(title=mgmt_sheet_name, rows=100, cols=10)
                    mgmt_ws.append_row(['구분', '티커', '종목명', '카테고리', '테마', '메모', '알림가', '시스템추천', '전문가의견', '정보링크'])
                else:
                    print(f"Debug Mode: '{mgmt_sheet_name}' sheet not found.")
                    return []
            
            current_mgmt = mgmt_ws.get_all_records()
            # 중복 체크 고도화
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
                category_raw = str(get_val(req, '카테고리', 'Category')).strip()
                category = self._map_category_to_korean(category_raw) # Korean mapping applied
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
                                        if not self.debug_mode:
                                            mgmt_ws.update_cell(idx, cat_col, category)
                                        print(f"Updated category for {found_ticker}: {category} (Dry-Run: {self.debug_mode})")
                                        break
                    else:
                        # 비활성화 이거나 검색결과가 FALSE인 경우 삭제 (v2.6.6)
                        if key in mgmt_keys:
                            keys_to_remove.add(key)
                            if key in mgmt_keys: mgmt_keys.remove(key)

            # 4. Batch Updates 실행
            
            # 4-1. 요청 시트의 검색결과 및 종목명 일괄 업데이트
            if status_updates:
                # [v2.7.0] 요청 시트 피드백 로그 (검증용)
                self._log_payload("watchlist_feedback", {
                    "status_updates": status_updates,
                    "name_updates": name_updates
                })

                if not self.debug_mode:
                    range_found = f"{gspread.utils.rowcol_to_a1(2, found_col_idx)}:{gspread.utils.rowcol_to_a1(len(requests)+1, found_col_idx)}"
                    rq_ws.update(values=[[s] for s in status_updates], range_name=range_found)
                    
                    range_name = f"{gspread.utils.rowcol_to_a1(2, name_col_idx)}:{gspread.utils.rowcol_to_a1(len(requests)+1, name_col_idx)}"
                    rq_ws.update(values=[[n] for n in name_updates], range_name=range_name)
                    print(f"Batch updated rq_ws: {len(status_updates)} items.")
                else:
                    print(f"Batch updated rq_ws (Dry-Run): {len(status_updates)} items would be updated.")

            # 4-2. 관리 시트에 새 종목 일괄 추가
            if rows_to_add:
                # [Important] Ensure categories in rows_to_add are also Korean mapped if not already
                # (They were mapped when created in recent loop)
                if not self.debug_mode:
                    mgmt_ws.append_rows(rows_to_add)
                    for r in rows_to_add: print(f"Added to management: {r[1]} ({r[3]})")
                else:
                    for r in rows_to_add: print(f"Added to management (Dry-Run): {r[1]} ({r[3]})")

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
                    if not self.debug_mode:
                        mgmt_ws.delete_rows(r_idx)
                        print(f"Removed row {r_idx} from management.")
                    else:
                        print(f"Removed row {r_idx} from management (Dry-Run).")

            return mgmt_ws.get_all_records()

        except Exception as e:
            print(f"Error in Stock List Sync: {e}")
            # 에러 발생 시 기존 방식대로라도 시도
            try:
                ws = self.sh.worksheet('종목_관리')
                return ws.get_all_records()
            except:
                return []

    def get_monthly_worksheet(self):
        """월별 탭 관리 및 반환 (target_date 기준): 7개 컬럼 구조 [v2.7.0]"""
        tab_name = self.target_date.strftime('%Y-%m')
        # [v2.7.0] 7-Column Schema
        headers = ['날짜', '지수/환율', '관심종목', '주요종목', '코인', '주의종목', '갱신날짜']
        
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
            ws = self.sh.add_worksheet(title=tab_name, rows=1000, cols=10)
            ws.update(values=[headers], range_name='A1')
            
            # 모든 컬럼 상단 정렬 적용
            try:
                fmt_top = CellFormat(verticalAlignment='TOP')
                format_cell_range(ws, 'A:G', fmt_top)  # A:G (7 cols)
                print(f"✅ 새 월별 시트 생성 및 서식 적용: {tab_name}")
            except Exception as e:
                print(f"✅ 새 월별 시트 생성: {tab_name} (서식 적용 실패: {e})")
            
            return ws

    def process_and_report(self, mode="AUTO", manual_date=False):
        print(f"--- Running Updater (v{VERSION}, Mode: {mode}, Target: {self.target_date}) ---")
        
        print("1. 동기화 중: 종목_요청 내역 반영...")
        watchlist_raw = self.get_stock_list()
        # [v2.7.0] 관리 시트 데이터 로그 (검증용)
        self._log_payload("watchlist_management", watchlist_raw)
        
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
        
        # [v2.6.9] 시장 유형별 개장 상태 세분화 (휴장/거래일/개장여부)
        target_iso = self.target_date.isoformat()
        is_kr_trading_day = (self.target_date.weekday() < 5)
        is_us_trading_day = (self.target_date.weekday() < 5)
        
        # [v2.7.0] 시장 실제로 개장 여부 (주말 강제 제외)
        is_kr_actually_open = (kospi_date == target_iso) and is_kr_trading_day
        is_us_actually_open = (sp500_date == target_iso) and is_us_trading_day

        # 리포트 및 오늘 시트 노출 여부 결정
        # 사용자의 MORNING/MIDDAY 등 세션 요구사항에 맞게 'Active' 상태 결정
        is_kr_active = is_kr_actually_open or (is_kr_trading_day and mode in ["MORNING", "MIDDAY", "CLOSE", "AUTO"])
        is_us_active = is_us_actually_open or (is_us_trading_day and mode in ["EVENING", "MORNING", "CLOSE", "AUTO"])
        
        if manual_date:
            # [v2.6.9] 과거 날짜(manual_date)인 경우, 평일이면 무조건 Active로 간주하여 데이터 수집 및 리포트 강제 생성
            # (지수 데이터 fetch 실패 시에도 종목 정보는 수집되어야 함)
            is_kr_active = is_kr_trading_day
            is_us_active = is_us_trading_day
            print(f">>> [Manual/History] Market Active Status FORCED by Trading Day: KR={is_kr_active}, US={is_us_active}")

        print(f">>> 시장 상태: 한국(Active={is_kr_active}, Today={is_kr_actually_open}), 미국(Active={is_us_active}, Today={is_us_actually_open})")
        
        # 하위 호환성을 위한 플래그 (스크래핑 등에 사용)
        is_kr_open = is_kr_active
        is_us_open = is_us_active

        print("3. 수집 중: 개인 관심종목 데이터...")
        watch_data = [] # 개인 관심종목
        major_data = [] # 주요 종목 (지수 옆 표시용)
        
        for item in watchlist_raw:
            ticker = str(item.get('Ticker', ''))
            if not ticker: continue
            
            # Use robust key matching
            category = str(item.get('카테고리', item.get('Category', ''))).strip().lower()
            if category in ['index', '지수']: continue # 지수는 이미 수집됨
            
            asset_type = item.get('Asset', item.get('구분', 'US'))
            
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

        # 3.5. 기록 중: '관심종목_관리' 종목 정보 업데이트 (우선순위 상향)
        print("3.5. 기록 중: '종목_관리' 종목 정보 업데이트...")
        # 수집된 최신 정보(가격, 추천 등)를 관리 시트에 반영
        try:
            mgmt_ws = self.sh.worksheet('종목_관리')
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
            print(f"Warning: 종목_관리 업데이트 중 실패: {e}")

        # [v2.7.0] 4. 수집 중: 주의종목 (순서 변경: 오늘 탭 갱신 전 수집)
        print("3.6. 수집 중: 주의종목 (네이버 증권 + FDR 전수 조사 + 자체 분석)...")
        
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
        
        # 4-3. 데이터 병합 및 중복 제거
        all_unusual_raw = naver_unusual + internal_unusual
        seen = set()
        current_unusual_items = []
        for d in all_unusual_raw:
            if d['Ticker'] not in seen:
                current_unusual_items.append(d)
                seen.add(d['Ticker'])
        
        # [v2.7.0] 버퍼링 로직 적용: 일간 누적 데이터 가져오기
        unusual = self.manage_cautionary_buffer(current_unusual_items)
        
        print(f">>> 최종 주의종목 (누적): {len(unusual)}개 (금번 발견 {len(current_unusual_items)}개)")

        print("3.7. 갱신 중: '오늘' 시트 (Dashboard Mode)...")
        # v2.6.9: is_active 플래그를 사용하여 노출 결정
        # unusual 리스트를 market_all과는 별개로 전달하여 '오늘'탭의 4번째 섹션에 표시
        self.update_today_data(market_all, indices, is_kr_active, is_us_active, mode=mode, cautionary_list=unusual)



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
        
        # 1. [관심종목] 섹션
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

        # 2. [주요종목] 섹션
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

        # 5. [v2.7.0] 텍스트 생성 (Calendar & Monthly 공용)
        print("5. 데이터 포맷팅 및 연동 준비...")
        
        # 5-1. 지수/환율 텍스트
        idx_report = []
        if indices:
            # KR
            kr_lines = [f"KR:{k} {v['rate']:+.2f}%" for k, v in indices.items() 
                        if k in ['KOSPI', 'KOSDAQ'] and v.get('category') in ['index', '지수']]
            if kr_lines: idx_report.append(", ".join(kr_lines))
            
            # US
            us_lines = [f"US:{k} {v['rate']:+.2f}%" for k, v in indices.items() 
                        if k in ['S&P500', 'NASDAQ'] and v.get('category') in ['index', '지수']]
            if us_lines: idx_report.append(", ".join(us_lines))
            
            # Exchange
            ex_lines = [f"{k} {v['price']:,.1f}" for k, v in indices.items() 
                        if v.get('category') in ['exchange', '환율']]
            if ex_lines: idx_report.append(", ".join(ex_lines))
        final_idx_text = "\n".join(idx_report)

        # 5-2. 섹션별 종목 텍스트 (Local filtering)
        major_data = [d for d in market_all if str(d.get('Category', '')).lower() in ['major', '주요']]
        crypto_data = [d for d in market_all if str(d.get('Category', '')).lower() in ['crypto', 'coin', '코인'] and d not in major_data]
        watch_data = [d for d in market_all if d not in major_data and d not in crypto_data]

        def format_item_list(items):
            lines = []
            for d in items:
                line = f"[{d.get('Ticker','-')}] {d.get('Name','-')} ({d.get('ChangeRate',0):+.2f}%)"
                if d.get('FinalRecommendation', '-') not in ['-', '']:
                    line += f" [{d.get('FinalRecommendation')}]"
                lines.append(line)
            return "\n".join(lines)

        text_watchlist = format_item_list(watch_data)
        text_major = format_item_list(major_data)
        text_crypto = format_item_list(crypto_data)

        # 5-3. 주의종목 텍스트
        caution_lines = []
        if unusual:
            for d in unusual:
                tag = f"[{d.get('Source', '주의')}]" if d.get('Source') != 'Internal' else "[급변]"
                line = f"{tag} {d.get('Name','-')} ({d.get('ChangeRate',0):+.2f}%)"
                caution_lines.append(line)
        text_cautionary = "\n".join(caution_lines)

        # 6. 연동: 구글 캘린더
        print("6. 연동 중: 구글 캘린더...")
        
        # 제목 생성
        now_kst = datetime.datetime.now()
        session_tag = ""
        if mode == "MORNING" or (7 <= now_kst.hour <= 9): session_tag = " [모닝]"
        elif mode == "MIDDAY" or (11 <= now_kst.hour <= 13): session_tag = " [미드데이]"
        elif mode == "CLOSE" or (15 <= now_kst.hour <= 17): session_tag = " [장마감]"
        elif mode == "EVENING" or (19 <= now_kst.hour <= 21): session_tag = " [이브닝]"

        if is_kr_open and is_us_open:
            cal_title = f"투자일지{session_tag} 📈 KOSPI {indices.get('KOSPI',{}).get('rate',0):+.2f}%"
        elif is_kr_open:
            cal_title = f"투자일지{session_tag}(국장) 📈 KOSPI {indices.get('KOSPI',{}).get('rate',0):+.2f}%"
        elif is_us_open:
            cal_title = f"투자일지{session_tag}(미장) 📈 S&P500 {indices.get('S&P500',{}).get('rate',0):+.2f}%"
        else:
            cal_title = f"투자일지{session_tag} 📅 시장 휴장"

        # 본문 생성 (New Structure)
        cal_desc_parts = []
        cal_desc_parts.append("## 📊 시장 지표")
        cal_desc_parts.append(final_idx_text if final_idx_text else "-")
        cal_desc_parts.append("")
        
        if text_watchlist:
            cal_desc_parts.append("## ⭐ 관심종목")
            cal_desc_parts.append(text_watchlist)
            cal_desc_parts.append("")
            
        if text_major:
            cal_desc_parts.append("## 📌 주요종목")
            cal_desc_parts.append(text_major)
            cal_desc_parts.append("")
            
        if text_crypto:
            cal_desc_parts.append("## 🪙 코인")
            cal_desc_parts.append(text_crypto)
            cal_desc_parts.append("")
            
        if text_cautionary:
            cal_desc_parts.append("## 🚨 주의종목 (전체)")
            cal_desc_parts.append(text_cautionary)
            cal_desc_parts.append("")

        cal_desc_parts.append("## 🔗 상세 내용 보기")
        cal_desc_parts.append(f"[구글 시트 바로가기](https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID})")
        
        self.create_calendar_event(cal_title, "\n".join(cal_desc_parts))

        # 7. 기록 중: 월별 일지 (7-Column Schema)
        print("7. 기록 중: 월별 일지...")
        try:
            m_ws = self.get_monthly_worksheet()
            target_date_str = self.target_date.strftime('%Y-%m-%d')
            
            # 행 찾기
            cell = None
            try:
                cell = m_ws.find(target_date_str, in_column=1)
            except: pass
            
            now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            row_data = [
                target_date_str,   # A: 날짜
                final_idx_text,    # B: 지수/환율
                text_watchlist,    # C: 관심종목
                text_major,        # D: 주요종목
                text_crypto,       # E: 코인
                text_cautionary,   # F: 주의종목 (누적)
                now_str            # G: 갱신날짜
            ]
            
            # [v2.6.9] 월별 데이터 로그
            self._log_payload("monthly", row_data)

            if cell:
                # Update existing row
                if not self.debug_mode:
                    m_ws.update(values=[row_data], range_name=f'A{cell.row}')
                    print(f"Updated monthly log for {target_date_str} (Row {cell.row})")
            else:
                # Append new row
                if not self.debug_mode:
                    m_ws.append_row(row_data)
                    print(f"Appended new monthly log for {target_date_str}")
                    
        except Exception as e:
            print(f"Error updating monthly log: {e}")

        print(f"모든 작업이 {self.target_date} 기준으로 완료되었습니다.")

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
            # [v2.6.9] 캘린더 데이터 로그
            self._log_payload("calendar", event)
            
            print(f">>> 새 이벤트 생성 중: {title}")
            if not self.debug_mode:
                res = self.calendar_service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
                print(f"✅ 캘린더 이벤트 생성 완료! Link: {res.get('htmlLink')}")
            else:
                print(f"✅ [DEBUG] 캘린더 이벤트 생성 스킵 (내용 로그 기록됨)")
            
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
