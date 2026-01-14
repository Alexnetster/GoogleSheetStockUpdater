import pandas as pd

def format_large_number(n):
    """
    Format large numbers into readable strings with suffixes (K, M, B, T).
    Example: 1,000,000 -> 1.00M
    """
    if n is None or pd.isna(n): return "-"
    if isinstance(n, str):
        # Already formatted (contains K, M, B, T)
        if any(s in n for s in ['K', 'M', 'B', 'T']): return n
        if n in ['-', 'N/A']: return n
        # Try to convert string to float
        try:
            n = float(n.replace(',', ''))
        except:
            return n # Return original if conversion fails
    
    try:
        val = float(n)
    except: return str(n)

    if val >= 1e12: return f"{val/1e12:,.2f}T"
    if val >= 1e9: return f"{val/1e9:,.2f}B"
    if val >= 1e6: return f"{val/1e6:,.2f}M"
    if val >= 1e3: return f"{val/1e3:,.2f}K"
    return f"{int(val):,}"

def format_price(n, asset_type):
    """
    Format price based on asset type.
    KR/KRW -> ₩1,000
    Others -> $1,000.00
    """
    if n is None or pd.isna(n): return "-"
    if asset_type in ['KR', 'KRW']: 
        return f"₩{int(n):,}"
    return f"${n:,.2f}"

def normalize_ticker(ticker, asset_type):
    """
    Normalize ticker symbols.
    KR: Ensure 6 digits (005930)
    US: Upper case
    """
    t = str(ticker).strip()
    # Handle float strings like '5930.0'
    if t.replace('.', '', 1).isdigit():
         try:
             t = str(int(float(t)))
         except: pass

    if asset_type == 'KR' and t.isdigit():
        return t.zfill(6)
    return t.upper() if asset_type == 'US' else t

def map_category_to_korean(category):
    """
    Map English/Legacy categories to Korean standard terms.
    """
    c = str(category).strip().lower()
    if c in ['index', '지수']: return '지수'
    if c in ['exchange', '환율']: return '환율'
    if c in ['major', '주요', '주요종목']: return '주요종목'
    if c in ['crypto', 'coin', '코인', '가상화폐']: return '가상화폐'
    if c in ['watchlist', '관심', '관심종목', '']: return '관심종목'
    return category # Fallback
