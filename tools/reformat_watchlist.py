import os
import json
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv()

CREDENTIALS_JSON = os.getenv('GOOGLE_CREDENTIALS_JSON')
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID', '').strip()

def migrate_watchlist_structure():
    if CREDENTIALS_JSON:
        info = json.loads(CREDENTIALS_JSON)
        creds = Credentials.from_service_account_info(info, scopes=['https://www.googleapis.com/auth/spreadsheets'])
    else:
        creds = Credentials.from_service_account_file('credentials.json', scopes=['https://www.googleapis.com/auth/spreadsheets'])
    
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)
    
    try:
        ws = sh.worksheet('관심종목_관리')
    except gspread.exceptions.WorksheetNotFound:
        print("Worksheet '관심종목_관리' not found.")
        return

    # 기존 데이터 로드
    all_values = ws.get_all_values()
    if not all_values:
        print("No data in worksheet.")
        return

    old_header = all_values[0]
    old_rows = all_values[1:]

    # 기존 인덱스 맵핑
    header_map = {name: i for i, name in enumerate(old_header)}
    
    # 새 구조 정의
    new_header = ['Country', 'Ticker', 'Name', 'Asset', 'Memo', 'Alert_Price', 'Recommendation', 'Expert_Opinion']
    new_rows = []

    for row in old_rows:
        def get_val(key, default=''):
            idx = header_map.get(key)
            return row[idx] if idx is not None and idx < len(row) else default

        asset = get_val('Asset', 'US')
        country = 'KR' if asset == 'KR' else 'US'
        
        new_row = [
            country,                             # Country
            get_val('Ticker'),                   # Ticker
            get_val('Name'),                     # Name
            asset,                               # Asset
            get_val('Memo'),                     # Memo
            get_val('Alert_Price'),              # Alert_Price
            get_val('Recommendation', '-'),      # Recommendation
            get_val('Expert_Opinion', '')        # Expert_Opinion
        ]
        new_rows.append(new_row)

    # 시트 초기화 후 다시 쓰기
    ws.clear()
    ws.update(values=[new_header] + new_rows, range_name='A1')
    
    print(f"Successfully migrated {len(new_rows)} items to new structure.")

if __name__ == "__main__":
    migrate_watchlist_structure()
