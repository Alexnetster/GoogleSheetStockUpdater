import requests
from bs4 import BeautifulSoup
import FinanceDataReader as fdr
import pandas as pd
import time

def get_kr_stock_listing() -> dict:
    """
    Fetch comprehensive KRX stock listing (Code: Name).
    """
    try:
        df = fdr.StockListing('KRX')
        return dict(zip(df['Code'], df['Name']))
    except Exception as e:
        print(f"Error fetching KRX listing: {e}")
        return {}

def get_latest_news(ticker: str) -> str:
    """
    Fetch the latest news title and link for a specific KR ticker from Naver Finance.
    """
    try:
        url = f"https://finance.naver.com/item/news_news.naver?code={ticker}"
        resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
        soup = BeautifulSoup(resp.text, 'html.parser')
        t = soup.select_one('.title a')
        if t:
            return f"{t.text.strip()} (https://finance.naver.com{t['href']})"
    except Exception as e:
        pass
    return "-"

def get_market_theme(ticker: str) -> str:
    """
    Fetch market themes for a KR ticker.
    """
    try:
        url = f"https://finance.naver.com/item/main.naver?code={ticker}"
        resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        themes = []
        for a in soup.find_all('a', href=True):
            if '/sise/theme.naver?field=name' in a['href']:
                themes.append(a.text.strip())
        
        if themes:
            return ", ".join(list(set(themes))[:3])
    except:
        pass
    return "-"

# --- Scraper Logic (from scraper.py) ---

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

def scrape_volume_surge(max_items=20):
    url = "https://finance.naver.com/sise/sise_quant_high.naver"
    try:
        time.sleep(1)
        response = requests.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        table = soup.select_one('table.type_2')
        if not table: return []
        
        stocks = []
        for row in table.select('tr'):
            cols = row.select('td')
            if len(cols) < 6: continue
            
            name_tag = cols[1].select_one('a')
            if not name_tag: continue
            
            try:
                name = name_tag.text.strip()
                href = name_tag.get('href', '')
                ticker = href.split('code=')[1].split('&')[0] if 'code=' in href else ''
                price = int(cols[2].text.strip().replace(',', ''))
                change_rate = float(cols[3].text.strip().replace('%', '').replace('+', '').replace('-', ''))
                if cols[3].text.strip().startswith('-'): change_rate *= -1
                volume = int(cols[5].text.strip().replace(',', ''))
                
                if ticker and price > 0:
                    stocks.append({
                        'Ticker': ticker, 'Name': name, 'Price': price,
                        'ChangeRate': change_rate, 'Volume': volume,
                        'Category': '거래량급증', 'Source': 'Naver', 'Asset': 'KR'
                    })
                if len(stocks) >= max_items: break
            except: continue
        return stocks
    except Exception as e:
        print(f"Error scraping volume surge: {e}")
        return []

def scrape_price_limit(max_items=20):
    stocks = []
    try:
        url = "https://finance.naver.com/sise/sise_upper.naver"
        time.sleep(1)
        response = requests.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        table = soup.select_one('table.type_2')
        
        if table:
            for row in table.select('tr'):
                cols = row.select('td')
                if len(cols) < 4: continue
                name_tag = cols[1].select_one('a')
                if not name_tag: continue
                try:
                    name = name_tag.text.strip()
                    href = name_tag.get('href', '')
                    ticker = href.split('code=')[1].split('&')[0] if 'code=' in href else ''
                    price = int(cols[2].text.strip().replace(',', ''))
                    if ticker and price > 0:
                        stocks.append({
                            'Ticker': ticker, 'Name': name, 'Price': price,
                            'ChangeRate': 29.9, 'Volume': 0,
                            'Category': '상한가', 'Source': 'Naver', 'Asset': 'KR'
                        })
                    if len(stocks) >= max_items: break
                except: continue
    except Exception as e:
        print(f"Error scraping price limit: {e}")
    return stocks

def analyze_market_fdr(min_volume=2000000, min_change=10.0, min_marcap=100000000000):
    if fdr is None: return {'upper': [], 'volume': [], 'rise': []}
    try:
        print(">>> FDR Market Scan (KRX)...")
        df = fdr.StockListing('KRX')
        if df is None or df.empty: return {'upper': [], 'volume': [], 'rise': []}
        
        results = {'upper': [], 'volume': [], 'rise': []}
        for _, row in df.iterrows():
            try:
                marcap = int(row['Marcap'])
                if marcap < min_marcap: continue
                
                stock_data = {
                    'Ticker': str(row['Code']), 'Name': str(row['Name']),
                    'Price': int(row['Close']), 'ChangeRate': float(row['ChagesRatio']),
                    'Volume': int(row['Volume']), 'Source': 'FDR', 'Asset': 'KR'
                }
                
                if stock_data['ChangeRate'] >= 29.5:
                    s = stock_data.copy()
                    s['Category'] = '상한가'
                    results['upper'].append(s)
                
                if stock_data['Volume'] >= min_volume:
                    s = stock_data.copy()
                    s['Category'] = '거래량급증'
                    results['volume'].append(s)

                if stock_data['ChangeRate'] >= min_change:
                    s = stock_data.copy()
                    s['Category'] = '급등'
                    results['rise'].append(s)
            except: continue
            
        return results
    except Exception as e:
        print(f"Error FDR scan: {e}")
        return {'upper': [], 'volume': [], 'rise': []}
