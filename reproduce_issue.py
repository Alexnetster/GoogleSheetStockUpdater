
import datetime
from src.domain.models import StockData
from src.services.pipeline import StockPipeline
from src.domain.models import AppConfig

def test_categorization():
    print("Testing Categorization Logic...")
    
    # Mock Data
    mock_data = [
        StockData(
            asset_type='KR',
            ticker='KRW-BTC',
            name='Bitcoin',
            price=100000000,
            change_rate=1.5,
            volume=500,
            market_cap=0,
            actual_date=datetime.date.today(),
            category='가상화폐' 
        ),
         StockData(
            asset_type='KR', 
            ticker='005930', 
            name='Samsung', 
            price=70000, 
            change_rate=0.5, 
            volume=1000000, 
            market_cap=0, 
            actual_date=datetime.date.today(), 
            category='주 식'
        )
    ]

    # Initialize Pipeline (mock config)
    config = AppConfig(spreadsheet_id='dummy', calendar_id='dummy')
    pipeline = StockPipeline(config)

    # Test
    cats, indices = pipeline._categorize_and_extract_indices(mock_data)
    
    print(f"Watchlist: {[s.name for s in cats['watchlist']]}")
    print(f"Crypto: {[s.name for s in cats['crypto']]}")

    # Assertion
    btc_in_crypto = any(s.name == 'Bitcoin' for s in cats['crypto'])
    if btc_in_crypto:
        print("PASS: Bitcoin is in Crypto category.")
    else:
        print("FAIL: Bitcoin is NOT in Crypto category.")

if __name__ == "__main__":
    test_categorization()
