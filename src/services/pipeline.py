import os
import datetime
import time
from src.domain.models import AppConfig, StockData
from src.adapters.google_sheets import GoogleSheetsAdapter
from src.adapters.google_calendar import CalendarAdapter
from src.adapters import naver_finance, yahoo_finance
from src.utils import formatters

class StockPipeline:
    def __init__(self, config: AppConfig):
        self.config = config
        self.sheets = GoogleSheetsAdapter(config.spreadsheet_id)
        self.calendar = CalendarAdapter(config.calendar_id)
        self.debug_mode = config.debug_mode
        self.target_date = config.target_date or datetime.date.today()
        
    def run(self, mode='AUTO'):
        print(f"Starting Pipeline... (Mode: {mode}, Date: {self.target_date}, Debug: {self.debug_mode})")
        
        # 1. Load Watchlist from '종목_관리'
        try:
            mgmt_rows = self.sheets.read_sheet_to_records('종목_관리')
            print(f"> Loaded {len(mgmt_rows)} items from Watchlist.")
        except Exception as e:
            print(f"Error reading watchlist: {e}")
            mgmt_rows = []

        # 2. Fetch Data (Sequential) for ALL items including indices
        stock_data_list = []
        
        print("2. Fetching Stock Data...")
        for row in mgmt_rows:
            try:
                ticker_raw = str(row.get('티커', '')).strip()
                asset = row.get('구분', 'KR')
                if not ticker_raw: continue
                
                # Normalize Ticker
                ticker = formatters.normalize_ticker(ticker_raw, asset)
                
                # Fetch Logic
                s_data = self._fetch_single_stock(ticker, asset, row)
                if s_data:
                    stock_data_list.append(s_data)
            except Exception as e:
                print(f"Error processing {row.get('종목명')}: {e}")
                
        # 2a. Fetch Unusual Stocks (KR Only)
        if mode in ['CLOSE', 'MIDDAY', 'AUTO']:
            print("2a. Analyzing Market (Volume/Limit)...")
            unusual_data = self._fetch_unusual_stocks()
        else:
            unusual_data = []

        # 3. Separate Indices & Categorize
        # Indices are now part of stock_data_list, identified by category 'Index', 'Exchange' etc.
        # We need to extract them for special header handling, but keeps them in data flow.
        
        categorized_data, indices_map = self._categorize_and_extract_indices(stock_data_list)
        categorized_data['unusual'] = unusual_data
        
        # 4. Build Dashboard Rows (For '오늘' Sheet)
        dashboard_rows = self._build_dashboard_rows(categorized_data, indices_map)
        
        # 5. Save Updates
        if not self.debug_mode:
            print("5. Updating Google Sheets...")
            # Update '오늘' Sheet
            self.sheets.clear_and_update('오늘', dashboard_rows)
            
            # Update Monthly Sheet
            self._update_monthly_sheet(categorized_data, indices_map)
            
            # Create Calendar Event
            self._create_calendar_event(mode, categorized_data, indices_map)
            
            print("✅ Pipeline execution completed successfully.")
        else:
            print("[Dry Run] Skipping Sheet/Calendar updates.")
            print(f"[Dry Run] Dashboard Rows Generated: {len(dashboard_rows)}")
            if indices_map:
                print(f"[Dry Run] Indices Found: {list(indices_map.keys())}")
            print(f"[Dry Run] Unusual Stocks Fetched: {len(unusual_data)}")

    def _fetch_unusual_stocks(self):
        # Use Naver/FDR adapter to find volume surge / upper limit / foreign buy
        try:
           # analyze_market_fdr returns dict: {'upper': [], 'volume': [], 'rise': []}
           results = naver_finance.analyze_market_fdr()
           
           # Also fetch foreign buy data
           foreign_buy = naver_finance.scrape_foreign_buy(max_items=10)
           
           # Convert to StockData list
           unusual_list = []
           
           # Priority: Upper Limit > Volume Surge > Foreign Buy > Rise
           seen_tickers = set()
           
           # Process FDR results
           for cat_key, items in results.items():
               for item in items:
                   ticker = item['Ticker']
                   if ticker in seen_tickers: continue
                   seen_tickers.add(ticker)
                   
                   s = StockData(
                       asset_type='KR',
                       ticker=ticker,
                       name=item['Name'],
                       price=item['Price'],
                       change_rate=item['ChangeRate'],
                       volume=item['Volume'],
                       market_cap=0,
                       actual_date=self.target_date,
                       formatted_price=formatters.format_price(item['Price'], 'KR'),
                       category=item['Category'],
                       recommendation='-',
                       expert_opinion=f"[{item['Source']}] {item['Category']}",
                       alert_price='-'
                   )
                   unusual_list.append(s)
           
           # Process foreign buy (may have Price=0, will be filled later if needed)
           for item in foreign_buy:
               ticker = item['Ticker']
               if ticker in seen_tickers: continue
               seen_tickers.add(ticker)
               
               s = StockData(
                   asset_type='KR',
                   ticker=ticker,
                   name=item['Name'],
                   price=item['Price'],
                   change_rate=item['ChangeRate'],
                   volume=item['Volume'],
                   market_cap=0,
                   actual_date=self.target_date,
                   formatted_price=formatters.format_price(item['Price'], 'KR') if item['Price'] > 0 else '-',
                   category=item['Category'],
                   recommendation='-',
                   expert_opinion=f"[{item['Source']}] {item['Category']}",
                   alert_price='-'
               )
               unusual_list.append(s)
                   
           return unusual_list[:20] # Limit to top 20
           
        except Exception as e:
            print(f"Error fetching unusual stocks: {e}")
            return []

    def _fetch_single_stock(self, ticker, asset, row_data) -> StockData:
        # ... (Same as before, simplified for brevity) ...
        try:
            name = row_data.get('종목명', ticker)
            category = row_data.get('카테고리', '')
            
            # Determine appropriate fetcher
            if asset == 'KR':
                end = self.target_date
                start = end - datetime.timedelta(days=7) # Look back 7 days
                
                hist = yahoo_finance.get_stock_history(ticker + '.KS', start, end)
                if hist.empty:
                    hist = yahoo_finance.get_stock_history(ticker + '.KQ', start, end)
                
                price = 0
                vol = 0
                change_rate = 0.0
                
                if not hist.empty:
                    price = hist['Close'].iloc[-1]
                    vol = hist['Volume'].iloc[-1]
                    if len(hist) >= 2:
                        prev_close = hist['Close'].iloc[-2]
                        if prev_close > 0:
                            change_rate = ((price - prev_close) / prev_close) * 100
                            
                news = naver_finance.get_latest_news(ticker)
                
                return StockData(
                    asset_type='KR', ticker=ticker, name=name,
                    price=price, change_rate=round(change_rate, 2),
                    volume=vol, market_cap=0, actual_date=end,
                    formatted_price=formatters.format_price(price, 'KR'),
                    news=news, 
                    category=category,
                    recommendation=row_data.get('시스템추천', '-'),
                    expert_opinion=row_data.get('전문가의견', '-'),
                    alert_price=row_data.get('알림가', '-')
                )
            
            elif asset in ['US', 'Coin']:
                end = self.target_date
                start = end - datetime.timedelta(days=7)
                
                sym = ticker
                if asset == 'Coin' and '-' not in ticker and 'USD' not in ticker:
                     sym = f"{ticker}-USD"
                
                hist = yahoo_finance.get_stock_history(sym, start, end)
                
                price = 0
                vol = 0
                change_rate = 0.0

                if not hist.empty:
                    price = hist['Close'].iloc[-1]
                    vol = hist['Volume'].iloc[-1]
                    if len(hist) >= 2:
                        prev_close = hist['Close'].iloc[-2]
                        if prev_close > 0:
                            change_rate = ((price - prev_close) / prev_close) * 100

                return StockData(
                    asset_type=asset, ticker=ticker, name=name,
                    price=price, change_rate=round(change_rate, 2), volume=vol,
                    market_cap=0, actual_date=end,
                    formatted_price=formatters.format_price(price, 'US' if asset == 'US' else 'Coin'),
                    category=category,
                    recommendation=row_data.get('시스템추천', '-'),
                    expert_opinion=row_data.get('전문가의견', '-'),
                    alert_price=row_data.get('알림가', '-')
                )

        except Exception as e:
            print(f"Fetch failed for {ticker}: {e}")
        return None

    def _categorize_and_extract_indices(self, stock_list):
        cats = {
            'watchlist': [],
            'major': [],
            'crypto': [],
            'etc': [],
            'unusual': []
        }
        indices_map = {}
        
        for s in stock_list:
            cat_lower = s.category.lower()
            
            # Check for Index/Exchange first
            if cat_lower in ['index', '지수', 'exchange', '환율']:
                # Add to indices map for special display
                indices_map[s.name] = {
                    'price': s.price,
                    'change': s.change_rate,
                    'fmt_price': s.formatted_price,
                    'fmt_change': f"{s.change_rate:+.2f}%",
                    'category': cat_lower
                }
                # Also prevent adding to regular lists if we want them HIDDEN from body
                # Usually indices appear at top, not in body.
                continue 

            if 'major' in cat_lower or '주요' in cat_lower:
                cats['major'].append(s)
            elif 'coin' in cat_lower or 'crypto' in cat_lower or '가상화폐' in cat_lower or s.asset_type == 'Coin':
                cats['crypto'].append(s)
            else:
                cats['watchlist'].append(s)
                
        return cats, indices_map

    def _build_dashboard_rows(self, categorized_data, indices_map):
        rows = []
        rows.append(['=== Daily Stock Dashboard ==='])
        rows.append(['Date', self.target_date.isoformat()])
        rows.append([])
        
        # 1. Indices Section
        if indices_map:
            rows.append(['[Market Indices]'])
            for name, data in indices_map.items():
                rows.append([name, data['fmt_price'], data['fmt_change']])
            rows.append([])

        # 2. Major Stocks
        rows.append(['[Major Stocks]'])
        rows.append(['Asset', 'Ticker', 'Name', 'Price', 'Change', 'News'])
        for s in categorized_data['major']:
            rows.append([s.asset_type, s.ticker, s.name, s.formatted_price, f"{s.change_rate:+}%", s.news])
        rows.append([])

        # 3. Watchlist
        rows.append(['[Watchlist]'])
        rows.append(['Asset', 'Ticker', 'Name', 'Price', 'Change', 'News'])
        for s in categorized_data['watchlist']:
            rows.append([s.asset_type, s.ticker, s.name, s.formatted_price, f"{s.change_rate:+}%", s.news])
        rows.append([])
        
        # 4. Crypto
        if categorized_data['crypto']:
            rows.append(['[Crypto]'])
            for s in categorized_data['crypto']:
                rows.append([s.asset_type, s.ticker, s.name, s.formatted_price, f"{s.change_rate:+}%"])
        
        # 5. Unusual (New)
        if categorized_data.get('unusual'):
            rows.append([])
            rows.append(['[Unusual Stocks (Alert)]'])
            for s in categorized_data['unusual']:
                rows.append(['KR', s.ticker, s.name, s.formatted_price, f"{s.change_rate:+}%", s.expert_opinion])

        return rows

    def _update_monthly_sheet(self, categorized_data, indices_map):
        ws = self.sheets.get_or_create_monthly_sheet(self.target_date)
        if not ws and not self.debug_mode: return

        # Helper for Formatting
        def format_items(items):
            lines = []
            for s in items:
                price_str = s.formatted_price
                try:
                    c_val = float(s.change_rate)
                    change_str = f"{c_val:+.2f}%"
                except:
                    change_str = "0.00%"
                
                rec = s.recommendation if s.recommendation not in ['-', ''] else '-'
                alert_val = s.alert_price
                target_str = f"목표가 {alert_val}" if alert_val and str(alert_val) not in ['-', '', '0'] else "-"
                source = "네이버증권" if s.asset_type == 'KR' else "Yahoo Finance"
                
                line = f"{s.ticker} / {s.name} / {price_str} / {change_str} / {rec} / {target_str} / {source}"
                lines.append(line)
            return "\n".join(lines)

        # Indices Text (Dynamic from map)
        idx_lines = []
        for name, data in indices_map.items():
            # Try to reconstruct format akin to legacy if possible
            # Legacy expected [KOSPI, KOSDAQ, S&P500, NASDAQ, USD/KRW]
            # Here we just output what we have.
            prefix = "KR" if 'KOS' in name or 'KRW' in name else "US" 
            if 'KRW' in name or '환율' in data['category']: prefix = "" # Exchange rate usually no prefix
            
            disp = f"{prefix}:{name} {data['fmt_change']}" if prefix else f"{name} {data['fmt_price']}"
            idx_lines.append(disp)
            
        final_idx_text = "\n".join(idx_lines)
        
        # Cautionary Buffer Text
        cautionary_items = categorized_data.get('unusual', [])
        # Simple format for monthly cell: [Category] Name (Change%)
        c_lines = []
        for s in cautionary_items:
            # Format 2: Reason / Ticker / Name / Price / Change / Source
            # Reason = s.category (e.g. 상한가)
            # Source = 네이버증권 (default for KR unusual)
            source = "네이버증권"
            line = f"{s.category} / {s.ticker} / {s.name} / {s.formatted_price} / {s.change_rate:+}% / {source}"
            c_lines.append(line)
        cautionary_text = "\n".join(c_lines)

        # Timestamp
        now_kst = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
        timestamp_str = now_kst.strftime("%Y-%m-%d %H:%M:%S")

        row = [
            self.target_date.isoformat(),
            final_idx_text,
            format_items(categorized_data['watchlist']),
            format_items(categorized_data['major']),
            format_items(categorized_data['crypto']),
            cautionary_text, 
            timestamp_str
        ]
        
        if self.debug_mode:
            print("[Dry Run] Monthly Row Prepared:", row)
        else:
            try:
                 cell = None
                 try:
                     cell = ws.find(self.target_date.isoformat())
                 except: pass
                 
                 if cell:
                     ws.update(f'A{cell.row}', [row])
                     print(f"Updated Monthly Log for {self.target_date}")
                 else:
                     ws.append_row(row)
                     print(f"Appended Monthly Log for {self.target_date}")
            except Exception as e:
                print(f"Error updating monthly sheet: {e}")

    def _create_calendar_event(self, mode, categorized_data, indices_map):
        report_lines = []
        
        def get_subset(data_list, asset_code):
            return [s for s in data_list if s.asset_type == asset_code]

        # 1. [관심종목]
        watch_list = categorized_data['watchlist']
        if watch_list:
            report_lines.append("=== [관심종목] ===")
            for code, name in [('KR', '한국'), ('US', '미국')]:
                subset = get_subset(watch_list, code)
                if subset:
                    report_lines.append(f"[관심종목:{name}]")
                    for s in subset:
                        rec_part = f" [{s.recommendation}]" if s.recommendation not in ['-', ''] else ""
                        report_lines.append(f"[{s.ticker}] {s.name} ({s.formatted_price} / {s.change_rate:+}%){rec_part}")
                    report_lines.append("")

        # 2. [주요종목]
        major_list = categorized_data['major']
        if major_list:
            report_lines.append("=== [주요종목] ===")
            for code, name in [('KR', '한국'), ('US', '미국'), ('Coin', '코인')]:
                subset = get_subset(major_list, code)
                if subset:
                    report_lines.append(f"[주요종목:{name}]")
                    for s in subset:
                        report_lines.append(f"[{s.ticker}] {s.name} ({s.formatted_price} / {s.change_rate:+}%)")
                    report_lines.append("")
        
        # 3. [특이종목]
        unusual_list = categorized_data.get('unusual', [])
        if unusual_list:
            report_lines.append("=== [특이종목] ===")
            report_lines.append(f"[특이종목:네이버/FDR수집:한국]") # Only KR supported for now
            for s in unusual_list:
                report_lines.append(f"{s.expert_opinion} {s.name} ({s.formatted_price} / {s.change_rate:+}%")
            report_lines.append("")

        detailed_info = "\n".join(report_lines) if report_lines else "데이터 없음"
        
        now_kst = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
        now_str = now_kst.strftime("%Y-%m-%d %H:%M:%S")
        
        cal_desc_parts = [
            f"🕒 기준: {now_str} (KR)",
            "",
            f"모드: {mode}",
            ""
        ]
        
        # Indices in Description
        if indices_map:
            cal_desc_parts.append("[시장 지표]")
            for k, v in indices_map.items():
                cal_desc_parts.append(f"- {k}: {v['fmt_price']} ({v['fmt_change']})")
            cal_desc_parts.append("")
            
        cal_desc_parts.append(detailed_info)
        
        final_desc = "\n".join(cal_desc_parts)
        title = f"📈 주식 시장 요약 ({self.target_date})"
        
        if not self.debug_mode:
            try:
                self.calendar.create_event(
                    summary=title,
                    description=final_desc,
                    start_time=datetime.datetime.now(),
                    end_time=datetime.datetime.now() + datetime.timedelta(minutes=15)
                )
                print("Calendar event created.")
            except Exception as e:
                print(f"Calendar error: {e}")
        else:
            print("[Dry Run] Calendar Event Body:")
            print(final_desc)
