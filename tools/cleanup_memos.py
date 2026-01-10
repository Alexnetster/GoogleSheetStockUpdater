import os
import json
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv()

CREDENTIALS_JSON = os.getenv('GOOGLE_CREDENTIALS_JSON')
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID', '').strip()

def cleanup_memos():
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

    # [Country, Ticker, Name, Asset, Memo, Alert_Price, Recommendation, Expert_Opinion]
    # Memo is index 4 (0-based)
    all_values = ws.get_all_values()
    if not all_values:
        return

    header = all_values[0]
    try:
        memo_idx = header.index('Memo')
    except ValueError:
        print("Memo column not found.")
        return

    updated_rows = 0
    for i, row in enumerate(all_values[1:], start=2):
        if len(row) > memo_idx and row[memo_idx] == "Moved/Added by Antigravity":
            ws.update_cell(i, memo_idx + 1, "")
            updated_rows += 1
            print(f"Cleared memo for row {i}")

    print(f"Successfully cleared {updated_rows} system memos.")

if __name__ == "__main__":
    cleanup_memos()
