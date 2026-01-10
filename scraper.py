"""
네이버 증권 웹 스크래핑 모듈

거래량 급증, 상한가/하한가, 외국인 순매수 등의 특이종목 정보를 수집합니다.
"""

import requests
from bs4 import BeautifulSoup
import time
from datetime import datetime

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
    url = "https://finance.naver.com/sise/sise_volume.naver"
    
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
    url = "https://finance.naver.com/sise/sise_foreign.naver"
    
    try:
        time.sleep(1)
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        table = soup.select_one('table.type_2')
        
        if not table:
            print("Warning: 외국인 순매수 테이블을 찾을 수 없습니다.")
            return []
        
        stocks = []
        rows = table.select('tr')
        
        for row in rows:
            cols = row.select('td')
            if len(cols) < 6:
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
                
                change_text = cols[3].text.strip().replace('%', '').replace('+', '')
                change_rate = float(change_text) if change_text.replace('-', '').replace('.', '').isdigit() else 0.0
                
                if ticker and name and price > 0:
                    stocks.append({
                        'Ticker': ticker,
                        'Name': name,
                        'Price': price,
                        'FormattedPrice': f"{price:,}원",
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
        
        print(f"✅ 외국인 순매수 종목 {len(stocks)}개 수집 완료")
        return stocks
        
    except Exception as e:
        print(f"Error: 외국인 순매수 스크래핑 실패: {e}")
        return []


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
