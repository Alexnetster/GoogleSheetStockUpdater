import os
import sys
import json
import datetime
import argparse

# 상위 디렉토리의 updater.py를 가져오기 위해 경로 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from updater import StockDataUpdater

class StockDataVerifier(StockDataUpdater):
    """실제 API 호출을 가로채고 로컬 파일로 로그를 남기는 검증용 클래스"""
    
    def __init__(self, target_date=None, dry_run=True, real_watchlist=False):
        super().__init__(target_date=target_date)
        self.debug_mode = dry_run # True이면 실제 API 호출(update 등)을 스킵함
        self.real_watchlist = real_watchlist
        # 2. 로그 디렉토리 생성 (및 초기화)
        self.log_dir = os.path.join("debug_logs", self.target_date.strftime("%Y-%m-%d"))
        if os.path.exists(self.log_dir):
            print(f"   [INFO] Cleaning up old logs in: {self.log_dir}")
            for f in os.listdir(self.log_dir):
                fp = os.path.join(self.log_dir, f)
                if os.path.isfile(fp):
                    os.unlink(fp)
        else:
            os.makedirs(self.log_dir, exist_ok=True)
            
        print(f"   [DEBUG] Verification mode active. Logs will be saved to: {self.log_dir}")
        if self.debug_mode:
            print("DEBUG: Dry-run active. Google Sheets/Calendar will NOT be modified.")
            # 가상 시트 데이터 설정 (API 호출 방지)
            self._mock_sheet_data()

    def _mock_sheet_data(self):
        """Dry-run 시 API 호출을 대신할 더미 데이터 설정"""
        print("DEBUG: Mocking Google Sheets & Calendar API for dry-run...")
        
        # 1. 캘린더 서비스 모의 객체 생성
        class MockService:
            def events(self):
                class MockEvents:
                    def list(self, **kwargs):
                        class MockList:
                            def execute(self): return {'items': []}
                        return MockList()
                    def delete(self, **kwargs):
                        class MockDelete:
                            def execute(self): return {}
                        return MockDelete()
                    def insert(self, **kwargs):
                        class MockInsert:
                            def execute(self): return {'htmlLink': 'http://localhost/mock-event'}
                        return MockInsert()
                return MockEvents()
        self.calendar_service = MockService()

        # 2. get_watchlist 가로채기 (real_watchlist 옵션 시 생략)
        if not self.real_watchlist:
            orig_get_watchlist = self.get_watchlist
            def mocked_get_watchlist():
                print("   [MOCK] Providing dummy watchlist data. (Use --real-watchlist to fetch actual)")
                return [
                    {'Asset': 'KR', 'Ticker': '005930', 'Name': '삼성전자', 'Category': 'Major', '메모': '삼성 반등 기원', '전문가의견': '매수'},
                    {'Asset': 'US', 'Ticker': 'AAPL', 'Name': 'Apple Inc.', 'Category': 'Major', 'Memo': '아이폰 호재'},
                    {'Asset': 'KR', 'Ticker': '000660', 'Name': 'SK하이닉스', 'Category': 'Watchlist', 'Recommendation': 'Buy'},
                    {'Asset': 'Coin', 'Ticker': 'BTC', 'Name': 'Bitcoin', 'Category': 'Crypto'}
                ]
            self.get_watchlist = mocked_get_watchlist
        else:
            print("   [INFO] Using REAL Watchlist data (Read-Only Mode).")

        # 3. get_market_indices 가로채기
        def mocked_get_indices():
            print("   [MOCK] Providing dummy market indices.")
            # 주말이면 직전 금요일 날짜로 셋팅하여 휴장 상황 시뮬레이션
            last_trading = self.target_date
            while last_trading.weekday() >= 5:
                last_trading -= datetime.timedelta(days=1)
            
            return {
                'KOSPI': {'price': 2500.0, 'change': 10.0, 'rate': 0.4, 'date': last_trading.isoformat(), 'category': 'index'},
                'S&P500': {'price': 4700.0, 'change': 20.0, 'rate': 0.42, 'date': last_trading.isoformat(), 'category': 'index'},
                'USD/KRW': {'price': 1300.0, 'change': 5.0, 'rate': 0.38, 'date': last_trading.isoformat(), 'category': 'exchange'}
            }
        self.get_market_indices = mocked_get_indices

        # 4. get_stock_data 가로채기 (실제 yfinance 호출 방지 및 속도 향상)
        def mocked_get_stock_data(tickers, asset_type):
            res = []
            for t in tickers:
                # Find category from self.watchlist (created in get_watchlist)
                category = "Watchlist" # Default
                if hasattr(self, 'watchlist'):
                    for item in self.watchlist:
                        if str(item.get('Ticker')) == str(t):
                             category = item.get('Category', 'Watchlist')
                             break
                
                res.append({
                    'Asset': asset_type, 'Ticker': t, 'Name': f"Mock_{t}", 
                    'Price': 100.0, 'FormattedPrice': '100.0', 'ChangeRate': 1.5, 
                    'Volume': 1000000, 'MarketCap': 1000000000, 'Recommendation': 'HOLD',
                    'Category': category
                })
            return res
        self.get_stock_data = mocked_get_stock_data

        # 5. 기타 시트 작업 가로채기 (get_monthly_worksheet 등)
        self.get_monthly_worksheet = lambda: None
        
    def _log_payload(self, target, data):
        # ... (생략 가능하지만 유지)
        file_path = os.path.join(self.log_dir, f"{target}.json")
        try:
            def json_default(obj):
                if isinstance(obj, (datetime.date, datetime.datetime)): return obj.isoformat()
                return str(obj)
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=json_default)
            print(f"   [LOG] Payload saved: {target}.json")
        except Exception as e:
            print(f"   [ERROR] Failed to save log {target}: {e}")

def main():
    parser = argparse.ArgumentParser(description="Stock Updater Sync Verification Tool")
    parser.add_argument("--date", help="Verification date (YYYY-MM-DD)", default=None)
    parser.add_argument("--mode", help="Session mode (MORNING, MIDDAY, CLOSE, EVENING, AUTO)", default="AUTO")
    parser.add_argument("--real", help="Actually update Google Sheets (Danger!)", action="store_true")
    parser.add_argument("--real-watchlist", help="Fetch real watchlist data while keeping dry-run safety", action="store_true")
    
    args = parser.parse_args()
    
    target_date = args.date or datetime.date.today().isoformat()
    is_dry_run = not args.real
    
    print(f"\n=== 🏁 Verification Start: {target_date} (DryRun={is_dry_run}) ===")
    
    try:
        verifier = StockDataVerifier(target_date=target_date, dry_run=is_dry_run, real_watchlist=args.real_watchlist)
        # process_and_report 실행 (manual_date=True로 설정하여 과거 데이터 처리 가능하도록 함)
        verifier.process_and_report(mode=args.mode, manual_date=True)
        
        print("\n=== ✨ Verification Summary ===")
        print(f"Location: {verifier.log_dir}")
        files = os.listdir(verifier.log_dir)
        for f in files:
            print(f" - ✅ {f}")
        
        # 간단한 자동 검증 로직
        today_rows_path = os.path.join(verifier.log_dir, "today_rows.json")
        if os.path.exists(today_rows_path):
            with open(today_rows_path, 'r', encoding='utf-8') as f:
                rows = json.load(f)
                print(f"\n[Today Sheet Analysis]")
                print(f"- Total rows generated: {len(rows)}")
                # 지수 포함 여부 확인
                has_indices = any("지수 및 환율" in str(r) for r in rows)
                print(f"- Market Indices included: {'Yes' if has_indices else 'No'}")
        
        monthly_path = os.path.join(verifier.log_dir, "monthly.json")
        if os.path.exists(monthly_path):
            with open(monthly_path, 'r', encoding='utf-8') as f:
                monthly_data = json.load(f)
                print(f"\n[Monthly Report Analysis]")
                print(f"- Items in payload: {len(monthly_data)}")
                print(f"- Report Summary Length: {len(monthly_data[1]) if len(monthly_data) > 1 else 0} chars")

        print("\n✅ Verification procedure completed successfully.")
        print("Tip: Check the .json files in debug_logs/ to see exactly what would be sent to Google.")

    except Exception as e:
        print(f"\n❌ Verification failed with error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
