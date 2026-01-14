import argparse
import os
import sys
from datetime import datetime

# Add project root to path so src modules can be imported
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.domain.models import AppConfig
from src.services.pipeline import StockPipeline

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass # Ignore if dotenv is not installed (e.g. production)

def main():
    parser = argparse.ArgumentParser(description='Google Sheet Stock Updater (Modular Refactored)')
    parser.add_argument('--date', type=str, help='Target Date (YYYY-MM-DD)')
    parser.add_argument('--debug', action='store_true', help='Enable debug mode (no write)')
    
    args = parser.parse_args()
    
    # Load Environment Variables
    spreadsheet_id = os.getenv('SPREADSHEET_ID', '').strip()
    calendar_id = os.getenv('CALENDAR_ID', 'primary').strip()
    
    if not spreadsheet_id:
        # Fallback for local dev if not set in env (Read from updater.py logic or manual)
        # For now, warn user
        print("Warning: SPREADSHEET_ID env var not set.")

    # Parse Date
    target_date = None
    if args.date:
        try:
            target_date = datetime.strptime(args.date, '%Y-%m-%d').date()
        except ValueError:
            print("Invalid date format. Use YYYY-MM-DD")
            return

    config = AppConfig(
        spreadsheet_id=spreadsheet_id,
        calendar_id=calendar_id,
        debug_mode=args.debug,
        target_date=target_date
    )
    
    pipeline = StockPipeline(config)
    pipeline.run()

if __name__ == '__main__':
    main()
