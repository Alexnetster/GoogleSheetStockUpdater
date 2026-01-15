import os
import sys
import json
import datetime
import argparse
import time
import io

# Windows Console Encoding Fix
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

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
        
        self.metrics = {}  # Performance metrics

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
            orig_get_watch = self.get_stock_list
            def mocked_get_stock_list():
                print("   [MOCK] Providing dummy watchlist data. (Use --real-watchlist to fetch actual)")
                data = [
                    {'Asset': 'KR', 'Ticker': '005930', 'Name': '삼성전자', 'Category': '주요종목', '메모': '삼성 반등 기원', '전문가의견': '매수', '시스템추천': '강력매수', '알림가': '80000', 'RequestEnabled': 'TRUE'},
                    {'Asset': 'US', 'Ticker': 'AAPL', 'Name': 'Apple Inc.', 'Category': '주요종목', 'Memo': '아이폰 호재', '전문가의견': 'BUY', '시스템추천': 'BUY', '알림가': '200', 'RequestEnabled': 'TRUE'},
                    {'Asset': 'KR', 'Ticker': '000660', 'Name': 'SK하이닉스', 'Category': '관심종목', 'Memo': '반도체 사이클', '전문가의견': 'HOLD', '시스템추천': 'HOLD', '알림가': '150000', 'RequestEnabled': 'TRUE'},
                    {'Asset': 'Coin', 'Ticker': 'BTC', 'Name': 'Bitcoin', 'Category': '가상화폐', 'Memo': '디지털 금', '전문가의견': '-', '시스템추천': '-', '알림가': '-', 'RequestEnabled': 'TRUE'}
                ]
                self.watchlist = data # [Crucial Fix] Save for get_stock_data to use
                return data
            self.get_stock_list = mocked_get_stock_list
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
                'KOSPI': {'price': 2500.0, 'change': 10.0, 'rate': 0.4, 'date': last_trading.isoformat(), 'category': '지수'},
                'S&P500': {'price': 4700.0, 'change': 20.0, 'rate': 0.42, 'date': last_trading.isoformat(), 'category': '지수'},
                'USD/KRW': {'price': 1300.0, 'change': 5.0, 'rate': 0.38, 'date': last_trading.isoformat(), 'category': '환율'}
            }
        self.get_market_indices = mocked_get_indices

        # 4. get_stock_data 가로채기 (실제 yfinance 호출 방지 및 속도 향상)
        def mocked_get_stock_data(tickers, asset_type):
            res = []
            for t in tickers:
                # Find category and other info from self.watchlist
                category = "관심종목"
                memo = ""
                sys_rec = "-"
                expert_op = "-"
                alert_price = "-"
                
                if hasattr(self, 'watchlist'):
                    for item in self.watchlist:
                        if str(item.get('Ticker')) == str(t):
                             category = item.get('Category', '관심종목')
                             memo = item.get('Memo', item.get('메모', ''))
                             sys_rec = item.get('시스템추천', item.get('SystemRecommendation', '-'))
                             expert_op = item.get('전문가의견', item.get('ExpertOpinion', '-'))
                             alert_price = item.get('알림가', item.get('AlertPrice', '-'))
                             break
                
                res.append({
                    'Asset': asset_type, 'Ticker': t, 'Name': f"Mock_{t}", 
                    'Price': 100.0, 'FormattedPrice': '100.0', 'ChangeRate': 1.5, 
                    'Volume': 1000000, 'MarketCap': 1000000000, 
                    'FinalRecommendation': 'HOLD', # Simulator fixed value
                    'Category': category,
                    'Memo': memo,
                    '시스템추천': sys_rec,
                    '전문가의견': expert_op,
                    '알림가': alert_price,
                    'ActualDate': self.target_date.isoformat()
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

    def run_validation(self):
        """저장된 로그 파일을 기반으로 필수 데이터 검증"""
        valid = True
        print("\n=== 🔍 Running Validation Checks ===")
        
        # 1. today_rows.json (오늘 탭 데이터)
        today_path = os.path.join(self.log_dir, "today_rows.json")
        if os.path.exists(today_path):
            with open(today_path, 'r', encoding='utf-8') as f:
                rows = json.load(f)
                if len(rows) < 5:
                    print("❌ [FAIL] Today sheet rows seem too few.")
                    valid = False
                else:
                    print(f"✅ [PASS] Today sheet generated {len(rows)} rows.")
                    
                # 필수 섹션 존재 여부
                content = json.dumps(rows, ensure_ascii=False)
                required = ["지수 및 환율", "주요종목", "관심종목"]
                for r in required:
                    # 섹션 헤더가 '=== [주요종목: 한국] ===' 처럼 저장되거나
                    # 텍스트로 저장되므로 단순 포함 여부 확인
                    if r not in content:
                        print(f"❌ [FAIL] Missing section: {r}")
                        # Debugging: Print first 500 chars of content to see what's wrong
                        print(f"   [DEBUG Content Snippet]: {content[:500]}...")
                        valid = False
                    else:
                        print(f"✅ [PASS] Found section: {r}")
        else:
             print("❌ [FAIL] today_rows.json not found.")
             valid = False

        # 2. monthly.json (월별 데이터) -> _log_payload가 monthly Update시 호출되지 않으면 없을 수 있음
        # pipeline상 _update_monthly_sheet 내부에서 _log_payload를 호출해야 함. 
        # (updater.py 수정 필요할 수 있음. 현재 updater.py는 monthly payload를 별도로 로그하지 않으므로 추가 필요할 수도 있으나,
        #  여기서는 파일 존재 여부만 체크하고 없으면 경고)
        
        return valid

def main():
    parser = argparse.ArgumentParser(description="Stock Updater Sync Verification Tool")
    parser.add_argument("--date", help="Verification date (YYYY-MM-DD)", default=None)
    parser.add_argument("--mode", help="Session mode (MORNING, MIDDAY, CLOSE, EVENING, AUTO)", default="AUTO")
    parser.add_argument("--real", help="Actually update Google Sheets (Danger!)", action="store_true")
    parser.add_argument("--real-watchlist", help="Fetch real watchlist data while keeping dry-run safety", action="store_true")
    parser.add_argument("--validate", help="Run automated validation after execution", action="store_true")
    
    args = parser.parse_args()
    
    target_date = args.date or datetime.date.today().isoformat()
    is_dry_run = not args.real
    
    print(f"\n=== 🏁 Verification Start: {target_date} (DryRun={is_dry_run}) ===")
    start_time = time.time()
    
    try:
        verifier = StockDataVerifier(target_date=target_date, dry_run=is_dry_run, real_watchlist=args.real_watchlist)
        # process_and_report 실행 (manual_date=True로 설정하여 과거 데이터 처리 가능하도록 함)
        # updater.py의 process_and_report는 main()에 있으므로, 여기서 비슷하게 실행하거나
        # updater.py 구조상 main logic을 method로 분리하는 게 좋음.
        # 일단 StockDataUpdater는 run() 같은 메인 메소드가 없음. updater.py의 main() 로직을 
        # 일부 복제하거나 리팩토링해야 함. 
        # * 임시 방편: verifier 인스턴스로 필요한 메소드들을 순차 호출 *
        
        # 1. 시세 수집
        # [Modified] Use process_and_report to verify Full Pipeline including Monthly Sheet
        print("\n=== Running Full Pipeline Verification (via process_and_report) ===")
        # Note: process_and_report calls get_stock_list, get_stock_data, get_market_indices internally.
        # These methods are overridden in StockDataVerifier to provide mock data.
        
        verifier.process_and_report(mode=args.mode, manual_date=bool(args.date))
        
        # Manual steps 1, 2, 3 removed as they are covered by process_and_report

        elapsed = time.time() - start_time
        print(f"\n✅ Execution Finished in {elapsed:.2f}s")
        
        if args.validate:
            if verifier.run_validation():
                print("\n✨ Validation PASSED")
                sys.exit(0)
            else:
                print("\n💀 Validation FAILED")
                sys.exit(1)
        else:
            print("\n=== ✨ Verification Summary ===")
            print(f"Location: {verifier.log_dir}")
            files = os.listdir(verifier.log_dir)
            for f in files:
                print(f" - ✅ {f}")

    except Exception as e:
        print(f"\n❌ Verification failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
