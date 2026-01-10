# Daily Stock & Crypto Sync Automation Plan

이 프로젝트는 한국/미국 주식 및 암호화폐 데이터를 자동으로 수집하여 Google Spreadsheet 및 Calendar에 동기화하는 것을 목표로 합니다.

## Goal Description
주식 및 코인 투자 데이터를 수동으로 확인하는 번거로움을 줄이기 위해, 매일 정해진 시간에 시장 데이터를 수집하고 분석하여 시각화된 시트와 일괄된 캘린더 요약을 제공하는 자동화 시스템을 구축합니다.

## Proposed Changes

### [Component] Data Collection & Processing (`updater.py`)
- **KR Stock**: `FinanceDataReader`를 사용하여 전 종목 리스트 및 명칭 매핑, `yfinance`로 가격 데이터 수집.
- **US Stock & Crypto**: `yfinance`를 사용하여 주요 지수 및 알트코인 데이터 수집.
- **Formatting**:
    - 숫자의 가독성을 위해 Large Number (K, M, B, T) 포맷터 구현.
    - `Price` 데이터에 천 단위 콤마(,) 및 국가별 특화 포맷 적용.
- **Analysis**: 당일 변동률 15% 이상인 종목을 '특이종목'으로 분류하고 관련 뉴스를 크롤링.

### [Component] Automation & Metadata (`daily_sync.yml`, `updater.py`)
- **GitHub Actions**: 1일 2회 (KST 16:00, 09:00) 주기적 실행 및 `push` 시 즉시 실행 워크플로우 보완.
- **Version Tracking**: 프로그램명(`DailyStockUpdater`)과 버전(`v1.1.0_20260110`)을 시트에 기록하여 실행 상태 추적 가능하도록 구현.

---

### [MODIFY] updater.py
- 숫 가독성 향상을 위한 `_format_large_number`, `_format_currency` 메서드 추가.
- 미국 주식 및 코인의 실제 이름(longName/shortName) 반영 로직 추가.
- 시트 상단에 버전 정보 및 업데이트 시각 기록 기능 추가.

### [NEW] docs/walkthrough.md
- 구현된 기능 설명 및 버전 이력 문서화.

## Verification Plan

### Automated Tests
- GitHub Actions의 `workflow_dispatch`를 통해 전체 프로세스를 수동 실행하여 데이터 정합성 확인.
- 로컬 환경에서 `python updater.py`를 실행하여 환경 변수 유효성 검증.

### Manual Verification
1. `글로벌데이터` 시트 확인: `삼성전자`, `Apple Inc.` 등 이름이 올바르게 표시되는지 확인.
2. `Volume`, `MarketCap` 열 확인: `1.23T`, `45.6M` 등의 단위가 적용되었는지 확인.
3. 구글 캘린더 확인: 요약 이벤트가 생성되었고 본문에 특이종목 뉴스가 포함되었는지 확인.
