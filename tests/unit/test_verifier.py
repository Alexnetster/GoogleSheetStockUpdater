
import unittest
import os
import sys
import json
from datetime import date

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import verify_sync

class TestVerificationData(unittest.TestCase):
    def setUp(self):
        # Run verify_sync with validate mode
        self.verifier = verify_sync.StockDataVerifier(target_date=date.today().isoformat(), dry_run=True)
    
    def test_mock_data_generation(self):
        """Test if mock data is generated correctly"""
        mock_watch = self.verifier.get_stock_list()
        self.assertTrue(len(mock_watch) > 0)
        self.assertEqual(mock_watch[0]['Ticker'], '005930')
        
    def test_log_file_creation(self):
        """Test if log files are created"""
        # Trigger minimal logic to generate logs
        try:
            self.verifier._log_payload("test_log", {"status": "ok"})
        except Exception:
            self.fail("Logging failed")
            
        log_file = os.path.join(self.verifier.log_dir, "test_log.json")
        self.assertTrue(os.path.exists(log_file))
        
if __name__ == '__main__':
    unittest.main()
