import os
import json
import argparse
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

# .env 로드
load_dotenv()

SPREADSHEET_ID = os.getenv('SPREADSHEET_ID')
CREDENTIALS_JSON = os.getenv('GOOGLE_CREDENTIALS_JSON')

# --- DB Schema 정의 (Source of Truth) ---
SHEET_SCHEMA = {
    '오늘': {
        'headers': ['항목', '값1', '값2', '값3', '값4', '값5', '값6'],
        'description': '당일 시장 현황 및 주요 종목 대시보드'
    },
    '관심종목_요청': {
        'headers': ['티커', '종목명', '카테고리', '메모', '사용여부', '검색결과'],
        'description': '사용자 입력 레이어 (종목 추가/삭제/분류)'
    },
    '관심종목_관리': {
        'headers': ['구분', '티커', '종목명', '카테고리', '테마', '메모', '알림가', '시스템추천', '전문가의견', '정보링크'],
        'description': '시스템 동기화 및 데이터 관리 레이어 (DB)'
    },
    '주의종목_버퍼': {
        'headers': ['Date', 'Asset', 'Ticker', 'Name', 'Price', 'Change', 'Volume', 'Source'],
        'description': '일간 특이 종목 누적 버퍼 (매일 초기화)'
    }
}

# --- 시스템 초기 기본 데이터 (Seed Data) ---
DEFAULT_BOOTSTRAP_DATA = [
    # [티커, 종목명, 카테고리, 메모, 사용여부, 검색결과]
    # Indices
    ['^KS11', 'KOSPI', 'Index', '국내 코스피 지수', 'TRUE', 'TRUE'],
    ['^KQ11', 'KOSDAQ', 'Index', '국내 코스닥 지수', 'TRUE', 'TRUE'],
    ['^GSPC', 'S&P500', 'Index', '미국 S&P500 지수', 'TRUE', 'TRUE'],
    ['^IXIC', 'NASDAQ', 'Index', '미국 나스닥 지수', 'TRUE', 'TRUE'],
    ['USDKRW=X', 'USD/KRW', 'Exchange', '원/달러 환율', 'TRUE', 'TRUE'],
    # Major Stocks
    ['005930', '삼성전자', 'Major', '국내 시총 1위', 'TRUE', 'TRUE'],
    ['000660', 'SK하이닉스', 'Major', '국내 반도체 주요', 'TRUE', 'TRUE'],
    ['AAPL', 'Apple', 'Major', '미국 시총 상위', 'TRUE', 'TRUE'],
    ['NVDA', 'Nvidia', 'Major', 'AI 반도체 리더', 'TRUE', 'TRUE'],
    ['TSLA', 'Tesla', 'Major', '전기차/자율주행', 'TRUE', 'TRUE'],
    # Crypto (24/7 Assets)
    ['BTC-USD', 'Bitcoin', 'Crypto', '크립토 대장주', 'TRUE', 'TRUE'],
    ['ETH-USD', 'Ethereum', 'Crypto', '알트코인 대장', 'TRUE', 'TRUE'],
    ['XRP-USD', 'Ripple', 'Crypto', '송금 최적화 코인', 'TRUE', 'TRUE'],
    ['SOL-USD', 'Solana', 'Crypto', '고성능 메인넷', 'TRUE', 'TRUE'],
]

def initialize(reset_mode=False):
    print("\n" + "="*50)
    print("   Google Sheet DB 초기화 및 복구 도구")
    print("="*50)
    
    if not SPREADSHEET_ID:
        print("Error: SPREADSHEET_ID 환경 변수가 없습니다.")
        return

    # 1. 인증
    if CREDENTIALS_JSON:
        info = json.loads(CREDENTIALS_JSON)
        creds = Credentials.from_service_account_info(info, scopes=['https://www.googleapis.com/auth/spreadsheets'])
    else:
        creds = Credentials.from_service_account_file('credentials.json', scopes=['https://www.googleapis.com/auth/spreadsheets'])
    
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)
    print(f"연결된 스프레드시트: {sh.title}")

    # 2. Reset 모드인 경우 모든 탭 삭제
    if reset_mode:
        print("\n[WARNING] 리셋 모드: 모든 기존 워크시트를 삭제합니다...")
        worksheets = sh.worksheets()
        
        # 최소 하나의 시트는 존재해야 하므로 임시 시트 생성
        temp_ws = sh.add_worksheet(title="TEMP_INIT", rows=1, cols=1)
        
        for ws in worksheets:
            try:
                sh.del_worksheet(ws)
                print(f"  - 삭제됨: {ws.title}")
            except Exception as e:
                print(f"  - 삭제 실패 ({ws.title}): {e}")
        
        print("모든 기존 탭이 삭제되었습니다.\n")

    # 3. 스키마에 따라 시트 생성 및 헤더 확인
    for name, config in SHEET_SCHEMA.items():
        try:
            ws = sh.worksheet(name)
            # 헤더 검증 및 자동 보정 (대시보드 성격의 '오늘' 탭은 제외)
            if name == '오늘':
                print(f"[{name}] 시트 확인 완료 (대시보드).")
                continue

            current_headers = ws.row_values(1)
            if current_headers != config['headers']:
                print(f"  ! [{name}] 헤더 구버전/불일치 발견. 최신 한글 헤더로 교체합니다.")
                # 맨 윗줄(A1)부터 헤더를 덮어씁니다. (기존 데이터는 유지됨)
                ws.update(values=[config['headers']], range_name='A1')
                print(f"  -> [{name}] 헤더 보정 완료.")
        except gspread.exceptions.WorksheetNotFound:
            print(f"[{name}] 시트 생성 중...")
            ws = sh.add_worksheet(title=name, rows=100, cols=len(config['headers']) + 1)
            ws.update(values=[config['headers']], range_name='A1')
            print(f"  -> [{name}] 생성 완료.")

            # '관심종목_요청' 탭인 경우 기본 데이터 주입
            if name == '관심종목_요청':
                print("  -> 시스템 기본 종목군(Indices & Major)을 채웁니다...")
                ws.append_rows(DEFAULT_BOOTSTRAP_DATA)

    # 4. 월별 탭(YYYY-MM) 헤더 보정
    print("\n[월별 일지] 시트 점검 중...")
    # [v2.7.0] 7-Column Schema
    monthly_headers = ['날짜', '지수/환율', '관심종목', '주요종목', '코인', '주의종목', '갱신날짜']
    for ws in sh.worksheets():
        # YYYY-MM 형식의 제목인지 확인
        title = ws.title
        if len(title) == 7 and title[4] == '-' and title[:4].isdigit() and title[5:].isdigit():
            current_headers = ws.row_values(1)
            if current_headers != monthly_headers:
                print(f"  ! [{title}] 월별 시트 헤더 보정 완료.")
                ws.update(values=[monthly_headers], range_name='A1')

    # 5. 임시 시트 삭제
    if reset_mode:
        try:
            temp_ws = sh.worksheet("TEMP_INIT")
            sh.del_worksheet(temp_ws)
        except:
            pass

    print("\n" + "="*50)
    print("✅ 초기화가 완료되었습니다.")
    print("  - Mode: " + ("RESET (Full Wipe)" if reset_mode else "Check & Repair"))
    print("="*50 + "\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Google Sheet DB Initializer")
    parser.add_argument("--reset", action="store_true", help="Delete all existing tabs and reset from scratch")
    parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")
    args = parser.parse_args()

    # Reset 모드 실행 전 한 번 더 물어보기 (사용자 편의)
    if args.reset and not args.yes:
        confirm = input("⚠️  정말로 모든 시트를 삭제하고 초기화하시겠습니까? (Y/N): ")
        if confirm.upper() != 'Y':
            print("작업을 취소합니다.")
            exit()
            
    initialize(reset_mode=args.reset)
