
import datetime
from src.domain.models import StockData, AppConfig
from src.services.pipeline import StockPipeline
from unittest.mock import MagicMock

def test_monthly_formatting():
    print("Testing Monthly Sheet Formatting (Format 2)...")
    
    # Mock Data
    cautionary_stock = StockData(
        asset_type='KR',
        ticker='005930',
        name='Samsung',
        price=70000,
        change_rate=2.5,
        volume=1000000,
        market_cap=0,
        actual_date=datetime.date.today(),
        category='상한가',
        formatted_price='70,000'
    )
    
    mock_categorized = {
        'watchlist': [],
        'major': [],
        'crypto': [],
        'unusual': [cautionary_stock]
    }
    mock_indices = {}

    # Mock Pipeline
    config = AppConfig(spreadsheet_id='dummy', calendar_id='dummy', debug_mode=True)
    pipeline = StockPipeline(config)
    
    # Mock Sheets Adapter to capture the output row
    pipeline.sheets = MagicMock()
    pipeline.sheets.get_or_create_monthly_sheet.return_value = MagicMock() # return a dummy sheet object
    
    # Run _update_monthly_sheet
    # Since debug_mode=True, it prints "[Dry Run] Monthly Row Prepared: [..., caution_text, ...]"
    # We can invoke it and capture stdout, OR we can just modify the test to print the line directly if we were unit testing.
    # But let's just run it and see the output in the console.
    
    pipeline._update_monthly_sheet(mock_categorized, mock_indices)

if __name__ == "__main__":
    test_monthly_formatting()
