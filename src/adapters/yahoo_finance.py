import yfinance as yf
import pandas as pd
from datetime import date, datetime

def get_stock_history(ticker: str, start_date: date, end_date: date) -> pd.DataFrame:
    """
    Fetch historical data using yfinance.
    Returns DataFrame with columns: Open, High, Low, Close, Volume, etc.
    """
    try:
        stock = yf.Ticker(ticker)
        # prepost=True needed for US socks to get pre/post market data if needed, 
        # though legacy code used it.
        hist = stock.history(start=start_date, end=end_date, prepost=True)
        return hist
    except Exception as e:
        print(f"Error fetching history for {ticker}: {e}")
        return pd.DataFrame()

def get_stock_info(ticker: str) -> dict:
    """
    Fetch stock metadata (name, sector, recommendations, etc.)
    """
    try:
        stock = yf.Ticker(ticker)
        return stock.info
    except Exception as e:
        print(f"Error fetching info for {ticker}: {e}")
        return {}
