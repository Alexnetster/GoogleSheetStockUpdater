import os
import argparse
import datetime
from dotenv import load_dotenv

# Ensure we can import from src if running from root
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from src.domain.models import AppConfig
from src.services.pipeline import StockPipeline

APP_NAME = "DailyStockUpdater (Refactored)"
VERSION = "v2.8.0-alpha"

def main():
    parser = argparse.ArgumentParser(description=f'{APP_NAME} {VERSION}')
    parser.add_argument('--date', type=str, help='Target Date (YYYY-MM-DD)')
    parser.add_argument('--mode', type=str, default='AUTO', help='Execution Mode: AUTO, MORNING, MIDDAY, CLOSE, EVENING')
    parser.add_argument('--debug', action='store_true', help='Enable Debug Mode (Dry-Run)')
    
    args = parser.parse_args()
    
    # 1. Load Environment Variables
    load_dotenv()
    
    debug_mode = args.debug
    target_date = None
    if args.date:
        try:
            target_date = datetime.datetime.strptime(args.date, '%Y-%m-%d').date()
        except ValueError:
            print("Invalid date format. Use YYYY-MM-DD")
            return

    print(f"--- {APP_NAME} {VERSION} ---")
    
    # 2. Config Setup
    try:
        config = AppConfig(
            spreadsheet_id=os.getenv('SPREADSHEET_ID'),
            calendar_id=os.getenv('CALENDAR_ID', 'primary'),
            debug_mode=debug_mode,
            target_date=target_date
        )
    except Exception as e:
        print(f"Configuration Error: {e}")
        return

    # 3. Initialize Pipeline
    pipeline = StockPipeline(config)
    
    # 4. Run Pipeline
    pipeline.run(mode=args.mode)

if __name__ == '__main__':
    main()
