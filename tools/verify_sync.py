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
    
    def __init__(self, target_date=None, dry_run=True):
        super().__init__(target_date=target_date)
        self.debug_mode = dry_run # True이면 실제 API 호출(update 등)을 스킵함
        self.log_dir = os.path.join("debug_logs", self.target_date.isoformat())
        os.makedirs(self.log_dir, exist_ok=True)
        print(f"DEBUG: Verification mode active. Logs will be saved to: {self.log_dir}")
        if self.debug_mode:
            print("DEBUG: Dry-run active. Google Sheets/Calendar will NOT be modified.")

    def _log_payload(self, target, data):
        """부모 클래스의 훅을 오버라이드하여 파일로 저장"""
        file_path = os.path.join(self.log_dir, f"{target}.json")
        try:
            # JSON 직렬화 가능 여부 확인 (datetime 등 처리)
            def json_default(obj):
                if isinstance(obj, (datetime.date, datetime.datetime)):
                    return obj.isoformat()
                raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=json_default)
            print(f"   [LOG] Payload saved: {file_path}")
        except Exception as e:
            print(f"   [ERROR] Failed to save log {target}: {e}")

def main():
    parser = argparse.ArgumentParser(description="Stock Updater Sync Verification Tool")
    parser.add_argument("--date", help="Verification date (YYYY-MM-DD)", default=None)
    parser.add_argument("--mode", help="Session mode (MORNING, MIDDAY, CLOSE, EVENING, AUTO)", default="AUTO")
    parser.add_argument("--real", help="Actually update Google Sheets (Danger!)", action="store_true")
    
    args = parser.parse_args()
    
    target_date = args.date or datetime.date.today().isoformat()
    is_dry_run = not args.real
    
    print(f"\n=== 🏁 Verification Start: {target_date} (DryRun={is_dry_run}) ===")
    
    try:
        verifier = StockDataVerifier(target_date=target_date, dry_run=is_dry_run)
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
