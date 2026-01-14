# 리팩토링 설계도: 아키텍처 이전 계획

이 문서는 현재의 레거시 구현체(`updater.py`)를 새로운 모듈형 아키텍처로 어떻게 옮길지 상세히 매핑한 문서입니다. **2단계(리팩토링)** 작업의 절대적인 기준이 됩니다.

## 1. 현재 상태 분석 (`updater.py`)

현재 시스템은 `StockDataUpdater` 라는 거대한 클래스(약 1700줄) 하나에 모든 기능이 섞여 있습니다:
1.  **인증 (Auth)**: 구글 인증 파일 로드.
2.  **입출력 (I/O)**: 구글 시트 및 캘린더 읽기/쓰기.
3.  **데이터 수집 (Data Fetching)**: 야후 파이낸스, 네이버 크롤링(`scraper.py` 의존).
4.  **비즈니스 로직 (Business Logic)**: 필터링 로직, 데이터 포맷팅, "세션(오전/오후)" 판단 로직.

## 2. 목표 아키텍처 (`src/`)

이 거대한 덩어리를 다음과 같은 구조로 쪼갤 것입니다:

```
src/
├── adapters/           # 외부 시스템과 대화하는 친구들
│   ├── google_sheets.py  # 구글 시트 처리 (배치 작업 포함)
│   ├── google_calendar.py# 구글 캘린더 처리
│   ├── naver_finance.py  # 네이버 크롤링 로직
│   └── yahoo_finance.py  # 야후/코인 데이터 처리
├── domain/             # 핵심 업무 로직 (API 호출 금지!)
│   ├── models.py         # 데이터 구조 정의 (StockData, MarketStatus 등)
│   └── analyzer.py       # 분석 로직 (예: 거래량 급증 판단)
├── services/           # 전체 조율자 (메인 로직)
│   └── pipeline.py       # 수집 -> 분석 -> 저장 흐름 제어
├── utils/              # 단순 도구들
│   ├── formatters.py     # 숫자/가격 예쁘게 꾸미기
│   └── time_utils.py     # 시간대(Timezone), 날짜 처리
└── main.py             # 실행 진입점 (CLI)
```

## 3. 함수 이동 매핑 테이블 (Function Mapping Table)

`updater.py`에 있는 함수들이 어디로 이사 가는지 정리한 표입니다.

| 기존 함수 (`updater.py`) | 이동할 모듈 (`src/...`) | 새로운 함수명 | 비고 |
| :--- | :--- | :--- | :--- |
| `__init__` | `main.py` & `services/pipeline.py` | `Pipeline.__init__` | 의존성 주입 설정 |
| `_load_credentials` | `utils/auth.py` (신규) | `get_google_credentials` | 환경변수 vs 파일 확인 |
| `_get_kr_name_map` | `adapters/naver_finance.py` | `get_kr_stock_listing` | FinanceDataReader 사용 |
| `_format_large_number` | `utils/formatters.py` | `format_large_number` | 순수 함수 (계산만 함) |
| `_format_price` | `utils/formatters.py` | `format_price` | 순수 함수 |
| `_normalize_ticker` | `utils/formatters.py` | `normalize_ticker` | 순수 함수 |
| `get_market_indices` | `services/pipeline.py` | `fetch_indices` (조율자) | 실제 호출은 어댑터에 위임 |
| `get_stock_data` | `services/pipeline.py` | `fetch_all_stocks` (조율자) | **비동기(Async) 적용 핵심 대상** |
| `update_today_data` | `adapters/google_sheets.py` | `DashboardSheet.update` | '오늘' 탭 로직 분리 |
| `_get_news_top1` | `adapters/naver_finance.py` | `get_latest_news` |뉴스 크롤링 로직 분리 |
| `manage_cautionary_buffer` | `adapters/google_sheets.py` | `BufferSheet.sync` | '주의종목_버퍼' 탭 로직 |
| `_map_category_to_korean` | `utils/formatters.py` | `map_category` | 순수 함수 |
| `get_stock_list` | `adapters/google_sheets.py` | `ConfigSheet.read` | '종목_관리' 탭 읽기 |
| `get_monthly_worksheet` | `adapters/google_sheets.py` | `ArchiveSheet.get` | 월별 아카이빙 처리 |
| `process_and_report` | `services/pipeline.py` | `run_pipeline` | **메인 실행 루프** |
| `_merge_report_sections` | `services/report_generator.py` | `merge_text_report` | 문자열 합치기 로직 |
| `create_calendar_event` | `adapters/google_calendar.py` | `CalendarAdapter.create_event` | |

## 4. 실행 전략 (Execution Strategy)

1.  **준비 (Setup)**: `src/` 폴더들과 `__init__.py` 파일들을 만듭니다.
2.  **도구부터 (Utils First)**: `formatters.py`, `auth.py` 같이 남에게 의존하지 않는 쉬운 것부터 옮깁니다.
3.  **어댑터 이동 (Adapters Second)**: `naver_finance.py`, `yahoo_finance.py` 같은 외부 연결 코드를 격리합니다.
4.  **시트 로직 (Sheets Adapter)**: GSpread 관련 코드를 `adapters/google_sheets.py`로 모읍니다.
5.  **조립 (Pipeline)**: 위 부품들을 `services/pipeline.py`에서 조립합니다.
6.  **마무리 (Entry Point)**: `main.py`를 만들고 기존 코드와 결과가 같은지 테스트합니다.

## 5. 위험 관리 (Risk Management)

*   **퇴로 확보**: 새로운 `main.py`가 완벽해질 때까지 기존 `updater.py`는 **절대로 지우지 않습니다.**
*   **검증**: "Dry Run(쓰기 없이 실행)" 모드로 두 스크립트를 돌려보고 로그가 똑같이 찍히는지 확인합니다.
