# 📊 Google Sheet Stock Updater

자동화된 주식/코인 시장 데이터 수집 및 구글 시트/캘린더 연동 시스템

## 🎯 주요 기능

- **자동 데이터 수집**: 한국/미국 주식, 주요 지수, 환율 정보 자동 수집
- **구글 시트 연동**: 글로벌데이터 시트 및 월별 일지 자동 업데이트
- **구글 캘린더 연동**: 일일 투자 일지를 캘린더 이벤트로 자동 생성
- **GitHub Actions 자동화**: 매일 자동 실행 (한국 시간 기준)

## 🏗️ 시스템 구조

```
┌─────────────────────┐
│  GitHub Actions     │  ← 매일 자동 실행 (서버리스)
│  (Python 환경)      │
└──────────┬──────────┘
           │
           ├─► 📈 데이터 수집 (yfinance, FinanceDataReader)
           │
           ├─► 📊 Google Sheets 업데이트
           │   ├─ 글로벌데이터 (시장 현황 + 주요 종목)
           │   └─ 월별 일지 (YYYY-MM 탭)
           │
           └─► 📅 Google Calendar 이벤트 생성
               └─ 일일 투자 일지
```

## ⚙️ 동작 환경

### 🔹 **실행 환경: GitHub Actions (클라우드)**

이 프로그램은 **GitHub Actions**에서 자동으로 실행됩니다:
- ✅ **로컬 환경 불필요**: 사용자 PC에 Python이나 라이브러리를 설치할 필요 없음
- ✅ **서버리스**: GitHub의 클라우드 서버에서 자동 실행
- ✅ **무료**: GitHub Actions 무료 사용량 내에서 운영

### 🔹 **로컬 실행 (선택사항)**

개발 또는 테스트 목적으로 로컬에서 실행하려면:

1. **환경 변수 설정 필요**:
   ```bash
   # Windows PowerShell
   $env:GOOGLE_CREDENTIALS_JSON = "JSON 내용"
   $env:SPREADSHEET_ID = "시트 ID"
   $env:CALENDAR_ID = "캘린더 ID"
   ```

2. **라이브러리 설치**:
   ```bash
   pip install -r requirements.txt
   ```

3. **실행**:
   ```bash
   python updater.py 2026-01-10
   ```

> ⚠️ **주의**: 로컬 실행은 개발/테스트 용도이며, 실제 운영은 GitHub Actions에서 자동으로 처리됩니다.

## 📋 설정 가이드

자세한 설정 방법은 [`docs/setup_guide.md`](docs/setup_guide.md)를 참조하세요.

### 필수 설정 항목

1. **Google Cloud 서비스 계정** 생성 및 JSON 키 다운로드
2. **Google Sheets** 공유 (서비스 계정 이메일로)
3. **Google Calendar** 공유 (서비스 계정 이메일로)
4. **GitHub Secrets** 등록:
   - `GOOGLE_CREDENTIALS_JSON`: 서비스 계정 JSON 키 전체 내용
   - `SPREADSHEET_ID`: 구글 시트 ID
   - `CALENDAR_ID`: 구글 캘린더 ID

## 📊 데이터 구조

### 글로벌데이터 시트

```
=== 📊 Market Summary ===
번호 | 국가 | 거래소   | 지수      | 변동폭
1    | 한국 | KOSPI    | 4,586.3   | +0.75%
2    | 한국 | KOSDAQ   | 947.9     | +0.41%
3    | 미국 | S&P500   | 6,966.3   | +0.65%
4    | 미국 | NASDAQ   | 23,671.3  | +0.81%
5    | 환율 | USD/KRW  | 1,450.1   | +0.27%

=== 📈 주요 종목 데이터 ===
Asset | Ticker | Name | Price | ChangeRate | Volume | MarketCap | Update
```

### 월별 일지 (YYYY-MM 탭)

```
Date       | Market Summary        | Watchlist Status | Unusual Stocks | Version
2026-01-09 | KR: KOSPI +0.75%...  | N/A              | [주요종목]...  | v1.4.0
```

## 🔄 자동 실행 스케줄

GitHub Actions는 다음 시간에 자동 실행됩니다 (한국 시간 기준):

- **매일 오전 9시**: 장 시작 전 데이터 수집
- **매일 오후 4시**: 장 마감 후 데이터 수집

> 스케줄 변경: `.github/workflows/daily_sync.yml` 파일의 `cron` 설정 수정

## 📁 프로젝트 구조

```
GoogleSheetStockUpdater/
├── updater.py              # 메인 프로그램
├── requirements.txt        # Python 의존성
├── .github/
│   └── workflows/
│       └── daily_sync.yml  # GitHub Actions 워크플로우
└── docs/
    ├── setup_guide.md      # 설정 가이드
    ├── walkthrough.md      # 기능 설명
    └── technical_spec.md   # 기술 사양
```

## 🛠️ 기술 스택

- **Python 3.x**
- **라이브러리**:
  - `yfinance`: 미국 주식/지수 데이터
  - `FinanceDataReader`: 한국 주식 데이터
  - `gspread`: Google Sheets API
  - `google-api-python-client`: Google Calendar API
  - `gspread-formatting`: 시트 서식 지정
- **자동화**: GitHub Actions

## 🌟 특별 기능

### 주말/휴장일 자동 감지

프로그램은 자동으로 주말을 감지하여 다르게 동작합니다:

- **평일**: 한국/미국 주식 + 지수 데이터 수집
- **주말**: 코인 데이터만 수집 (24/7 거래)
  - 캘린더 제목: `[날짜] 📅 주말 (주식 시장 휴장)`
  - 주식 시장 휴장 안내 포함

### 시트 구조 관리

- **새 월별 탭**: 최신 구조로 자동 생성
- **기존 탭**: 구조 변경은 사용자가 수동으로 수행
- **데이터 업데이트**: 프로그램이 자동으로 처리

> 💡 **참고**: 기존 시트의 구조를 변경하려면 구글 시트에서 직접 컬럼을 추가/삭제하세요. 프로그램은 데이터만 업데이트합니다.

## 📝 버전 히스토리

- **v1.4.0** (2026-01-10):
  - 주말/휴장일 자동 감지 기능 추가
  - 주말에는 코인 데이터만 수집
  - 캘린더 이벤트 주말 전용 형식 추가
  - 월별 시트 Indices Info 컬럼 제거 (중복 제거)
  - 새 월별 탭 생성 시 최신 구조 자동 적용

- **v1.3.0** (2026-01-10):
  - 글로벌데이터 시트에 시장 현황 요약 추가
  - 월별 일지의 Market Summary/Indices Info 여러 줄 표시
  - 캘린더 ID 설정 가이드 개선

## 📞 문의 및 기여

이슈나 개선 사항은 GitHub Issues를 통해 제안해주세요.

## 📄 라이선스

MIT License
