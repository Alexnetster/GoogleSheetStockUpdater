# 주식 데이터 갱신 및 분석 프로젝트 기술 명세서 (Technical Spec)

이 문서는 프로그램의 상세 설계, 데이터 구조 및 운영 로직을 설명합니다.

## 프로젝트 개요
Google Spreadsheet 및 Calendar API를 연동하여 자산별(국장, 미장, 코인) 데이터를 수집하고, 특이 종목을 분석하여 일괄된 요약본을 제공하는 자동화 시스템입니다.

## 핵심 컴포넌트 (`updater.py`)
- **API 인증**: Google 서비스 계정(`credentials.json`)을 사용하여 Sheets/Calendar API 권한 획득.
- **데이터 수집 엔진**:
    - **국장**: `FinanceDataReader`로 종목 리스트 획득, `yfinance`로 가격 수집.
    - **미장/코인**: `yfinance`를 활용한 글로벌 데이터 수집.
- **가독성 포맷터**: `Large Number` (K, M, B, T) 단위 변환 및 `Price` 콤마(,) 표시 로직.
- **분석기 (Analyzer)**:
    - **특이 종목**: 상/하한가 및 등락률 15% 이상 종목 추출.
    - **뉴스 연동**: 네이버 금융(KR) 및 Yahoo Finance(Global) 뉴스 크롤링.
- **출력기 (Exporter)**:
    - Google Sheets: `글로벌데이터`(갱신), `일별기록`(누적), `특이종목_테마`(누적).
    - Google Calendar: 요약 이벤트 생성.

## 데이터 구조 (Google Sheets)

### 1. 글로벌데이터 (Overwrite Mode)
- **Asset**: KR / US / Coin
- **Ticker**: 종목 코드
- **Name**: 종목 명칭
- **Price**: 현재가 (포맷팅 적용)
- **ChangeRate**: 등락률 (%)
- **Volume / MarketCap**: 거래량 및 시가총액 (단위 변환 적용)

### 2. 일별기록 (Append Mode)
- **Date**: 실행 날짜
- **Summary**: 시장 전체 요약 및 특이 종목 초록
- **Version**: 실행된 프로그램 버전

### 3. 특이종목_테마 (Append Mode)
- **Date**: 발생 날짜
- **Ticker/Name**: 종목 정보
- **ChangeRate**: 등락률
- **News**: 수집된 뉴스 헤드라인 및 링크

---

## 특이 종목 선정 기준 (Filtering Criteria)
1. **변동폭 상위**: 당일 등락률 +/- 15.0% 이상.
2. **거래량 폭증**: (추후 보완 예정) 최근 20일 평균 대비 일정 배수 이상.
3. **상/하한가**: 주요 시장별 상설 가격 제한폭 부근.

## 자동화 스케줄링 (GitHub Actions)
- **데일리 동기화**: 매일 정해진 시간(오후 4시, 오전 9시)에 cron 스케줄로 자동 실행.
- **이벤트 트리거**: `main` 또는 `dev` 브랜치에 `push` 발생 시 즉시 실행.
