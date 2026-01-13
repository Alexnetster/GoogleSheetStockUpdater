import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from updater import StockDataUpdater
import json

def debug_watchlist():
    updater = StockDataUpdater()
    # Debug mode to avoid writes, but we just want to read
    updater.debug_mode = True 
    
    print("Fetching stock list...")
    # This calls the method that reads '종목_관리'
    items = updater.get_stock_list()
    
    print(f"Total items: {len(items)}")
    
    kr_items = [i for i in items if i.get('Asset') == 'KR' or i.get('구분') == 'KR']
    us_items = [i for i in items if i.get('Asset') == 'US' or i.get('구분') == 'US']
    
    print(f"KR Items: {len(kr_items)}")
    print(f"US Items: {len(us_items)}")
    
    print("\n--- First 5 KR Items ---")
    for i in kr_items[:5]:
        print(i)

    print("\n--- First 5 US Items ---")
    for i in us_items[:5]:
        print(i)

if __name__ == "__main__":
    debug_watchlist()
