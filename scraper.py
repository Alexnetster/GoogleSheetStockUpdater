"""
네이버 증권 웹 스크래핑 모듈

거래량 급증, 상한가/하한가, 외국인 순매수 등의 특이종목 정보를 수집합니다.
"""

import requests
from bs4 import BeautifulSoup
import time
from datetime import datetime
import pandas as pd
try:
    import FinanceDataReader as fdr
except ImportError:
    fdr = None

# 요청 헤더 (봇 차단 방지)
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

def scrape_volume_surge(max_items=20):
    """
    거래량 급증 종목 스크래핑
    
    Returns:
        list: 거래량 급증 종목 리스트
    """
    url = "https://finance.naver.com/sise/sise_quant_high.naver"
    
    try:
        time.sleep(1)  # 요청 간 딜레이
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 테이블 찾기
        table = soup.select_one('table.type_2')
        if not table:
            print("Warning: 거래량 급증 테이블을 찾을 수 없습니다.")
            return []
        
        stocks = []
        rows = table.select('tr')
        
        for row in rows:
            cols = row.select('td')
            if len(cols) < 6:
                continue
            
            # 종목명 링크 확인
            name_tag = cols[1].select_one('a')
            if not name_tag:
                continue
            
            try:
                # 데이터 추출
                name = name_tag.text.strip()
                href = name_tag.get('href', '')
                
                # 종목코드 추출
                ticker = ''
                if 'code=' in href:
                    ticker = href.split('code=')[1].split('&')[0]
                
                # 현재가
                price_text = cols[2].text.strip().replace(',', '')
                price = int(price_text) if price_text.isdigit() else 0
                
                # 등락률
                change_text = cols[3].text.strip().replace('%', '').replace('+', '')
                change_rate = float(change_text) if change_text.replace('-', '').replace('.', '').isdigit() else 0.0
                
                # 거래량
                volume_text = cols[5].text.strip().replace(',', '')
                volume = int(volume_text) if volume_text.isdigit() else 0
                
                if ticker and name and price > 0:
                    stocks.append({
                        'Ticker': ticker,
                        'Name': name,
                        'Price': price,
                        'FormattedPrice': f"{price:,}원",
                        'ChangeRate': change_rate,
                        'Volume': volume,
                        'Category': '거래량급증',
                        'Source': 'Naver',
                        'Asset': 'KR'
                    })
                
                if len(stocks) >= max_items:
                    break
                    
            except Exception as e:
                print(f"Warning: 거래량 급증 데이터 파싱 오류: {e}")
                continue
        
        print(f"✅ 거래량 급증 종목 {len(stocks)}개 수집 완료")
        return stocks
        
    except Exception as e:
        print(f"Error: 거래량 급증 스크래핑 실패: {e}")
        return []


def scrape_price_limit(max_items=20):
    """
    상한가/하한가 종목 스크래핑
    
    Returns:
        list: 상한가/하한가 종목 리스트
    """
    stocks = []
    
    # 상한가
    try:
        url = "https://finance.naver.com/sise/sise_upper.naver"
        time.sleep(1)
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        table = soup.select_one('table.type_2')
        
        if table:
            rows = table.select('tr')
            for row in rows:
                cols = row.select('td')
                if len(cols) < 4:
                    continue
                
                name_tag = cols[1].select_one('a')
                if not name_tag:
                    continue
                
                try:
                    name = name_tag.text.strip()
                    href = name_tag.get('href', '')
                    ticker = href.split('code=')[1].split('&')[0] if 'code=' in href else ''
                    
                    price_text = cols[2].text.strip().replace(',', '')
                    price = int(price_text) if price_text.isdigit() else 0
                    
                    if ticker and name and price > 0:
                        stocks.append({
                            'Ticker': ticker,
                            'Name': name,
                            'Price': price,
                            'FormattedPrice': f"{price:,}원",
                            'ChangeRate': 29.9,  # 상한가 근사치
                            'Volume': 0,
                            'Category': '상한가',
                            'Source': 'Naver',
                            'Asset': 'KR'
                        })
                    
                    if len(stocks) >= max_items:
                        break
                        
                except Exception as e:
                    continue
        
        print(f"✅ 상한가 종목 {len(stocks)}개 수집 완료")
        
    except Exception as e:
        print(f"Error: 상한가 스크래핑 실패: {e}")
    
    return stocks


def scrape_foreign_buy(max_items=20):
    """
    외국인 순매수 종목 스크래핑
    
    Returns:
        list: 외국인 순매수 종목 리스트
    """
    # 외국인 순매수 상위 (거래소 기준, iframe URL 직접 호출)
    url = "https://finance.naver.com/sise/sise_deal_rank_iframe.naver?sosok=01&investor_gubun=9000&type=buy"
    
    try:
        time.sleep(1)
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # '외국인 순매수' 제목이 있는 섹션의 테이블 찾기
        # 이 페이지는 여러 테이블이 있으므로 a.tltle 클래스가 있는 행을 포함한 테이블을 찾습니다.
        stocks = []
        
        # 모든 테이블을 순회하며 데이터 추출 시도
        tables = soup.select('table')
        for table in tables:
            rows = table.select('tr')
            for row in rows:
                cols = row.select('td')
                # 외국인 순매수 테이블은 종목명이 td[0] 또는 td[1]에 위치함
                name_tag = row.select_one('a.tltle')
                if not name_tag:
                    continue
                
                try:
                    name = name_tag.text.strip()
                    href = name_tag.get('href', '')
                    ticker = href.split('code=')[1].split('&')[0] if 'code=' in href else ''
                    
                    # sise_deal_rank 페이지는 현재가/등락률 정보가 보통 없습니다.
                    # 대신 순매수량 등이 있습니다. 여기서는 0으로 처리하거나 
                    # 필요한 경우 추후 상세 페이지에서 가져와야 합니다.
                    price = 0
                    change_rate = 0.0
                    
                    if ticker and name:
                        # 중복 방지
                        if any(s['Ticker'] == ticker for s in stocks):
                            continue
                            
                        stocks.append({
                            'Ticker': ticker,
                            'Name': name,
                            'Price': price,
                            'FormattedPrice': "N/A",
                            'ChangeRate': change_rate,
                            'Volume': 0,
                            'Category': '외국인순매수',
                            'Source': 'Naver',
                            'Asset': 'KR'
                        })
                    
                    if len(stocks) >= max_items:
                        break
                        
                except Exception as e:
                    continue
            
            if len(stocks) >= max_items:
                break
        
        if not stocks:
            print("Warning: 외국인 순매수 데이터를 찾을 수 없습니다.")
        else:
            print(f"✅ 외국인 순매수 종목 {len(stocks)}개 수집 완료")
            
        return stocks
        
    except Exception as e:
        print(f"Error: 외국인 순매수 스크래핑 실패: {e}")
        return []


def analyze_market_fdr(min_volume=2000000, min_change=10.0, min_marcap=100000000000):
    """
    FinanceDataReader를 사용한 시장 전수 조사 (상한가, 거래량 급증 종목 추출)
    
    Args:
        min_volume (int): 최소 거래량 기준 (기본 200만)
        min_change (float): 최소 변동률 기준 (기본 10%)
        min_marcap (int): 최소 시가총액 기준 (기본 1000억)
        
    Returns:
        dict: 상한가(upper), 거래량급증(volume), 급등락(rise) 종목 리스트를 담은 딕셔너리
    """
    if fdr is None:
        print("Error: FinanceDataReader가 설치되어 있지 않습니다.")
        return {'upper': [], 'volume': [], 'rise': []}
        
    try:
        print(f">>> FDR 시장 전수 조사 시작... (대상: KRX)")
        df = fdr.StockListing('KRX')
        
        if df is None or df.empty:
            print("Warning: 시장 데이터를 불러올 수 없습니다.")
            return {'upper': [], 'volume': [], 'rise': []}
            
        results = {
            'upper': [],   # 상한가 종목 (약 29.5% 이상)
            'volume': [],  # 거래량 급증 (단순 거래량 기준)
            'rise': []     # 급등 종목 (10% 이상)
        }
        
        # 데이터 정제 (ChagesRatio가 소수점 단위일 수 있으므로 확인 필요)
        # FDR 최근 버전은 0.14 형태로 0.14%를 의미하거나 14.0 형태로 14%를 의미할 수 있음
        # 관찰된 결과 ChagesRatio=0.14는 0.14%를 의미함.
        
        for _, row in df.iterrows():
            try:
                ticker = str(row['Code'])
                name = str(row['Name'])
                price = int(row['Close'])
                change_rate = float(row['ChagesRatio'])
                volume = int(row['Volume'])
                marcap = int(row['Marcap'])
                
                # 시가총액 필터 (기본 1000억 이상)
                if marcap < min_marcap:
                    continue
                    
                stock_data = {
                    'Ticker': ticker,
                    'Name': name,
                    'Price': price,
                    'FormattedPrice': f"{price:,}원",
                    'ChangeRate': change_rate,
                    'Volume': volume,
                    'Source': 'FDR',
                    'Asset': 'KR'
                }

                # 1. 상한가 종목 (약 29.5% 이상)
                if change_rate >= 29.5:
                    stock_data['Category'] = '상한가'
                    results['upper'].append(stock_data)
                    
                # 2. 거래량 급증 종목 (기준 거래량 이상)
                if volume >= min_volume:
                    # 상한가와 구분하기 위해 카테고리 설정
                    v_data = stock_data.copy()
                    v_data['Category'] = '거래량급증'
                    results['volume'].append(v_data)
                    
                # 3. 급등 종목 (10% 이상)
                if change_rate >= min_change:
                    r_data = stock_data.copy()
                    r_data['Category'] = '급등'
                    results['rise'].append(r_data)
                    
            except Exception:
                continue
        
        # 정렬
        results['upper'] = sorted(results['upper'], key=lambda x: x['ChangeRate'], reverse=True)[:20]
        results['volume'] = sorted(results['volume'], key=lambda x: x['Volume'], reverse=True)[:20]
        results['rise'] = sorted(results['rise'], key=lambda x: x['ChangeRate'], reverse=True)[:20]
        
        print(f"✅ FDR 조사 완료: 상한가 {len(results['upper'])}개, 고거래량 {len(results['volume'])}개, 급등 {len(results['rise'])}개")
        return results
        
    except Exception as e:
        print(f"Error: FDR 시장 조사 실패: {e}")
        return {'upper': [], 'volume': [], 'rise': []}


if __name__ == "__main__":
    # 테스트
    print("=== 네이버 증권 스크래핑 테스트 ===")
    
    print("\n1. 거래량 급증 종목:")
    volume_stocks = scrape_volume_surge(5)
    for stock in volume_stocks:
        print(f"  - {stock['Name']} ({stock['FormattedPrice']} / {stock['ChangeRate']:+.2f}%)")
    
    print("\n2. 상한가 종목:")
    upper_stocks = scrape_price_limit(5)
    for stock in upper_stocks:
        print(f"  - {stock['Name']} ({stock['FormattedPrice']})")
    
    print("\n3. 외국인 순매수 종목:")
    foreign_stocks = scrape_foreign_buy(5)
    for stock in foreign_stocks:
        print(f"  - {stock['Name']} ({stock['FormattedPrice']} / {stock['ChangeRate']:+.2f}%)")
        
    print("\n4. FDR 시장 전수 조사 (주말/마감용):")
    fdr_results = analyze_market_fdr(min_volume=5000000)
    print("  [상한가]")
    for s in fdr_results['upper'][:5]:
        print(f"  - {s['Name']} ({s['FormattedPrice']} / {s['ChangeRate']:+.2f}%)")
    print("  [고거래량]")
    for s in fdr_results['volume'][:5]:
        print(f"  - {s['Name']} ({s['FormattedPrice']} / 거래량: {s['Volume']:,})")
