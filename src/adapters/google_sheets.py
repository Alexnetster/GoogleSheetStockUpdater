import gspread
import pandas as pd
from datetime import datetime
from src.utils.auth import get_google_credentials
from src.domain.models import StockData

class MockCell:
    def __init__(self, row, col):
        self.row = row
        self.col = col

class MockWorksheet:
    def __init__(self, title):
        self.title = title
    
    def find(self, query, in_column=None):
        print(f"[Mock] Searching for '{query}' in {self.title}...")
        # Simulate finding today's date to test AUTH/UPDATE path
        if "2026-01-14" in query: 
            return MockCell(5, 1) # Pretend found at row 5
        return None # Not found
        
    def col_values(self, col):
        return []
    
    def update(self, range_name, values):
        print(f"[Mock] Updating {self.title} range {range_name} with {values}")
        
    def append_row(self, values):
        print(f"[Mock] Appending to {self.title}: {values}")

class GoogleSheetsAdapter:
    def __init__(self, spreadsheet_id: str):
        try:
            self.creds = get_google_credentials()
            self.gc = gspread.authorize(self.creds)
            self.sh = self.gc.open_by_key(spreadsheet_id)
        except Exception as e:
            print(f"Warning: Could not authorize Google Sheets ({e}). Entering Mock Mode.")
            self.gc = None
            self.sh = None
        self.id = spreadsheet_id

    def read_sheet_to_records(self, sheet_name: str) -> list:
        if not self.sh:
            print(f"[Mock] Reading {sheet_name} -> []")
            return []
        try:
            ws = self.sh.worksheet(sheet_name)
            return ws.get_all_records()
        except Exception as e:
            print(f"Error reading {sheet_name}: {e}")
            return []

    def clear_and_update(self, sheet_name: str, values: list):
        if not self.sh:
            print(f"[Mock] Updating {sheet_name} with {len(values)} rows")
            return
        try:
            ws = self.sh.worksheet(sheet_name)
            ws.clear()
            if values:
                ws.update(range_name='A1', values=values)
        except Exception as e:
            print(f"Error updating {sheet_name}: {e}")

    def append_rows(self, sheet_name: str, values: list):
        if not self.sh:
            print(f"[Mock] Appending to {sheet_name}: {len(values)} rows")
            return
        try:
            ws = self.sh.worksheet(sheet_name)
            ws.append_rows(values)
        except Exception as e:
            print(f"Error appending to {sheet_name}: {e}")

    def get_or_create_monthly_sheet(self, target_date):
        if not self.sh:
             print(f"[Mock] Get Monthly Sheet for {target_date}")
             return MockWorksheet(target_date.strftime('%Y-%m'))
        title = target_date.strftime('%Y-%m')
        try:
            return self.sh.worksheet(title)
        except gspread.WorksheetNotFound:
            ws = self.sh.add_worksheet(title=title, rows=1000, cols=20)
            ws.append_row(['날짜', '지수/환율', '관심종목', '주요종목', '코인', '주의종목', '갱신날짜시간(KR)'])
            return ws

    # Specific Logic for Specific Tabs can be methods here or in Service
    # For Phase 2, we keep 'Mechanism' here. Logic construction will be in Pipeline.
