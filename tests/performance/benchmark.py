
import unittest
import time
import os
import sys

# Add project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import verify_sync

class PerformanceBenchmark(unittest.TestCase):
    def test_sync_performance(self):
        """Measure execution time of critical sync flow (Dry-Run)"""
        start_time = time.time()
        
        # Setup Verifier (Mocked)
        verifier = verify_sync.StockDataVerifier(dry_run=True)
        
        # Execute Pipeline Steps
        mock_watch = verifier.get_stock_list()
        
        tickers_kr = [r['Ticker'] for r in mock_watch if r.get('Asset') == 'KR']
        tickers_us = [r['Ticker'] for r in mock_watch if r.get('Asset') == 'US']
        
        # Mock Fetch
        verifier.get_stock_data(tickers_kr, 'KR')
        verifier.get_stock_data(tickers_us, 'US')
        verifier.get_market_indices()
        
        # Mock Update
        verifier.update_today_data([], {}, mode='AUTO')
        
        duration = time.time() - start_time
        print(f"\n[Perf] Sync Pipeline Duration: {duration:.4f}s")
        
        # Threshold: Should be under 2 seconds for mocked run
        self.assertLess(duration, 2.0, "Performance is too slow!")

if __name__ == '__main__':
    unittest.main()
