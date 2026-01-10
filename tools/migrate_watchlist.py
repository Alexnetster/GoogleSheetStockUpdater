import os
import json
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv()

CREDENTIALS_JSON = os.getenv('GOOGLE_CREDENTIALS_JSON')
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID', '').strip()

def add_to_watchlist(tickers):
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

    existing_tickers = ws.col_values(1)
    
    rows_to_add = []
    for ticker in tickers:
        if ticker not in existing_tickers:
            # Ticker, Name, Asset, Memo, Alert_Price
            rows_to_add.append([ticker, '', 'US', 'Moved/Added by Antigravity', ''])
            print(f"Adding {ticker} to watchlist.")
        else:
            print(f"{ticker} already exists.")
            
    if rows_to_add:
        ws.append_rows(rows_to_add)
        print(f"Successfully added {len(rows_to_add)} tickers.")
    else:
        print("No new tickers to add.")

if __name__ == "__main__":
    new_tickers = [
        'PLTR', 'IONQ', 'RGTI', 'SOXL', 'TQQQ', # Moved
        'PLTU', 'POET', 'NVDL', 'QLD', 'TSLL', 'FIG', 'HIMZ', 'JOBY' # Newly added
    ]
    add_to_watchlist(new_tickers)
