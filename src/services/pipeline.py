import os
import datetime
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
        
    def run(self):
        print(f"Starting Pipeline... (Debug: {self.config.debug_mode})")
        
        # 1. Load Watchlist
        # For Phase 2, we simplify: just read '종목_요청' logic if needed, 
        # but legacy 'process_and_report' mainly updates '오늘' and '종목_관리'.
        
        # In legacy, 'get_stock_list' synced Config -> Management.
        # We will assume Management is up to date or we can port that sync logic later.
        # For now, let's focus on the 'Update Dashboard' flow.
        
        try:
            mgmt_rows = self.sheets.read_sheet_to_records('종목_관리')
        except:
            print("Could not read '종목_관리'. skipping.")
            mgmt_rows = []

        # 2. Fetch Data (Sequential for now)
        stock_data_list = []
        for row in mgmt_rows:
            try:
                # Basic parsing from row
                ticker_raw = str(row.get('티커', '')).strip()
                asset = row.get('구분', 'KR')
                if not ticker_raw: continue
                
                # Normalize Ticker (Important for KR stocks: 5930 -> 005930)
                ticker = formatters.normalize_ticker(ticker_raw, asset)
                
                # Fetch Logic (Delegated)
                s_data = self._fetch_single_stock(ticker, asset, row)
                if s_data:
                    stock_data_list.append(s_data)
            except Exception as e:
                print(f"Error processing {row}: {e}")
                
        # 3. Fetch Indices
        indices = self._fetch_indices()
        
        # 4. Filter & Organize (Simplified logic from updater.py)
        # In legacy, it separates into sections.
        
        # 5. Build Dashboard Rows
        dashboard_rows = self._build_dashboard_rows(stock_data_list, indices)
        
        # 6. Save
        if not self.config.debug_mode:
            self.sheets.clear_and_update('오늘', dashboard_rows)
            
            # Update Monthly Sheet
            self._update_monthly_sheet(stock_data_list, indices)
            
            # self.calendar.create_event(...) # Optional implementation
            print("Dashboard updated.")
        else:
            print("[Dry Run] Dashboard rows generated:", len(dashboard_rows))
            # Dry run logic for monthly: Call with mock adapter
            print("[Dry Run] Testing Monthly Sheet Upsert Logic...")
            self._update_monthly_sheet(stock_data_list, indices)

    def _fetch_single_stock(self, ticker, asset, row_data) -> StockData:
        # Wrapper to choose source
        # This is a simplified version of legacy 'get_stock_data' loop
        try:
            name = row_data.get('종목명', ticker)
            
            if asset == 'KR':
                # Yahoo for price, Naver for aux? Legacy used Yahoo for price mostly.
                # Let's use Yahoo for price history (consistent with legacy)
                end = datetime.date.today()
                start = end - datetime.timedelta(days=7)
                hist = yahoo_finance.get_stock_history(ticker + '.KS', start, end)
                if hist.empty:
                    hist = yahoo_finance.get_stock_history(ticker + '.KQ', start, end)
                
                if not hist.empty:
                    price = hist['Close'].iloc[-1]
                    vol = hist['Volume'].iloc[-1]
                    
                    # Calculate Change Rate
                    change_rate = 0.0
                    if len(hist) >= 2:
                        prev_close = hist['Close'].iloc[-2]
                        if prev_close > 0:
                            change_rate = ((price - prev_close) / prev_close) * 100
                            
                    # Naver News
                    news = naver_finance.get_latest_news(ticker)
                    
                    return StockData(
                        asset_type='KR', ticker=ticker, name=name,
                        price=price, change_rate=round(change_rate, 2),
                        volume=vol, market_cap=0, actual_date=end,
                        formatted_price=formatters.format_price(price, 'KR'),
                        news=news, category=row_data.get('카테고리', '')
                    )
            
            elif asset == 'US':
                end = datetime.date.today()
                start = end - datetime.timedelta(days=7)
                hist = yahoo_finance.get_stock_history(ticker, start, end)
                if not hist.empty:
                    price = hist['Close'].iloc[-1]
                    
                    # Calculate Change Rate
                    change_rate = 0.0
                    if len(hist) >= 2:
                        prev_close = hist['Close'].iloc[-2]
                        if prev_close > 0:
                            change_rate = ((price - prev_close) / prev_close) * 100

                    return StockData(
                        asset_type='US', ticker=ticker, name=name,
                        price=price, change_rate=round(change_rate, 2), volume=0,
                        market_cap=0, actual_date=end,
                        formatted_price=formatters.format_price(price, 'US'),
                        category=row_data.get('카테고리', '')
                    )

        except Exception as e:
            print(f"Fetch failed for {ticker}: {e}")
        return None

    def _fetch_indices(self):
        """
        Fetch key market indices using Yahoo Finance.
        Targets: USD/KRW, KOSPI, S&P 500
        """
        indices = {}
        targets = {
            'USD/KRW': 'KRW=X',
            'KOSPI': '^KS11',
            'S&P 500': '^GSPC'
        }
        
        end = datetime.date.today()
        start = end - datetime.timedelta(days=7)

        for name, ticker in targets.items():
            try:
                hist = yahoo_finance.get_stock_history(ticker, start, end)
                if not hist.empty:
                    price = hist['Close'].iloc[-1]
                    # Calculate change from previous close (simple method)
                    # Ideally we want previous session close
                    if len(hist) >= 2:
                        prev = hist['Close'].iloc[-2]
                        change = ((price - prev) / prev) * 100
                    else:
                        change = 0.0
                        
                    indices[name] = {
                        'price': price,
                        'change': change,
                        'fmt_price': f"{price:,.2f}" if 'KRW' not in name else f"{price:,.2f}",
                        'fmt_change': f"{change:+.2f}%"
                    }
            except Exception as e:
                print(f"Error fetching index {name}: {e}")
                
        return indices

    def _build_dashboard_rows(self, stock_list, indices):
        # Reconstruct the 'Today' sheet layout
        rows = []
        rows.append(['=== 2026 Stock Dashboard ==='])
        rows.append(['Date', datetime.datetime.now().isoformat()])
        rows.append([])
        
        # Indices
        rows.append(['[Indices]'])
        # ... Add indices logic
        rows.append([])

        # Stocks
        rows.append(['[Stocks]'])
        rows.append(['Asset', 'Ticker', 'Name', 'Price', 'News'])
        for s in stock_list:
            rows.append([s.asset_type, s.ticker, s.name, s.formatted_price, s.news])
            
        return rows

    def _update_monthly_sheet(self, stock_list, indices):
        """
        Updates the monthly log sheet (YYYY-MM).
        Format: $Price / ChangeRate%
        Upsert logic: Updates row if date exists, otherwise appends.
        """
        today_str = (self.config.target_date or datetime.date.today()).isoformat()
        ws = self.sheets.get_or_create_monthly_sheet(self.config.target_date or datetime.date.today())
        if not ws: return

        # 1. Categorize
        watchlist_items = []
        major_items = []
        coin_items = []
        
        for s in stock_list:
            # Use Category to filter out Indices/Exchange rates if they are in the list
            # We already fetch indices separately in _fetch_indices()
            cat = s.category
            if '지수' in cat or '환율' in cat:
                continue

            # Format: $Price / ChangeRate%
            price_str = s.formatted_price
            change_str = f"{s.change_rate:+}%" if s.change_rate else "0%"
            display_str = f"{s.name}({price_str} / {change_str})"
            
            # Debug Print for first few items
            if len(watchlist_items) < 3 and '관심' in cat:
                 print(f"[DEBUG_FMT] {s.name} -> P:{price_str} C:{change_str} => {display_str}")
            
            if '관심' in cat: watchlist_items.append(display_str)
            elif '주요' in cat: major_items.append(display_str)
            elif '코인' in cat or '가상' in cat or s.asset_type == 'Coin': coin_items.append(display_str)
            else: watchlist_items.append(display_str) # Default
            
        # 2. Build Row
        # Columns: ['날짜', '지수/환율', '관심종목', '주요종목', '코인', '주의종목', '갱신날짜']
        
        # Indices String Formatting
        # Ex: "USD/KRW: 1,405.50 (+0.12%)\nKOSPI: 2,500.00 (-0.50%)"
        indices_list = []
        for name, data in indices.items():
            # Special formatting for Exchange Rate (KRW is usually just price)
            val_str = f"{name}: {data['fmt_price']} ({data['fmt_change']})"
            indices_list.append(val_str)
        
        if not indices_list:
            indices_str = ""
        else:
            indices_str = "\n".join(indices_list)

        # Cautionary Buffer - Placeholder for now
        cautionary_str = "(System Update: Cautionary logic pending)"
        
        # KST Timestamp
        now_kst = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
        timestamp_str = now_kst.strftime("%Y-%m-%d %H:%M:%S")
        
        row = [
            today_str,
            indices_str,
            "\n".join(watchlist_items),
            "\n".join(major_items),
            "\n".join(coin_items),
            cautionary_str, 
            timestamp_str
        ]
        
        # 3. Upsert (Check if date exists)
        try:
            # cell = ws.find(today_str, in_column=1) # find matches exact string usually
            # Some gspread versions require in_column, some don't support it well.
            # Safe way: get all records or find.
            # Let's try find() which is standard.
            cell = None
            try:
                 cell = ws.find(today_str)
            except gspread.exceptions.CellNotFound:
                 cell = None
            
            if cell and cell.col == 1:
                # Update existing row
                row_idx = cell.row
                ws.update(range_name=f'A{row_idx}', values=[row])
                print(f"Updated existing row {row_idx} in monthly sheet: {ws.title}")
            else:
                # Append new row
                ws.append_row(row)
                print(f"Appended to monthly sheet: {ws.title}")
        except Exception as e:
            print(f"Error updating monthly sheet: {e}")
