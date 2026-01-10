# 로컬 개발 환경 설정 가이드

## 📋 .env 파일 설정

로컬에서 테스트하려면 `.env` 파일을 생성해야 합니다.

### 1. `.env.example` 복사
```bash
copy .env.example .env
```

### 2. `.env` 파일 편집

`.env` 파일을 열고 실제 값으로 변경하세요:

```env
# Google Service Account Credentials (JSON 형식 그대로 한 줄로)
GOOGLE_CREDENTIALS_JSON={"type":"service_account","project_id":"your-project-id",...}

# Google Spreadsheet ID (스프레드시트 URL에서 추출)
# https://docs.google.com/spreadsheets/d/[SPREADSHEET_ID]/edit
SPREADSHEET_ID=1abc...xyz

# Google Calendar ID (기본값: primary)
CALENDAR_ID=primary
```

### 3. Credentials JSON 가져오기

**방법 1: GitHub Secrets에서 복사**
1. GitHub 저장소 → Settings → Secrets and variables → Actions
2. `GOOGLE_CREDENTIALS_JSON` 값 복사
3. `.env` 파일에 붙여넣기 (한 줄로)

**방법 2: GCP Console에서 다운로드**
1. Google Cloud Console → IAM & Admin → Service Accounts
2. 서비스 계정 선택 → Keys → Add Key → Create new key → JSON
3. 다운로드한 JSON 파일 내용을 한 줄로 만들어 붙여넣기

```bash
# PowerShell에서 JSON을 한 줄로 변환
(Get-Content credentials.json -Raw) -replace '\s+', ' '
```

## 🧪 로컬 테스트

### 1. 라이브러리 설치
```bash
pip install -r requirements.txt
```

### 2. 스크래퍼만 테스트
```bash
python scraper.py
```

예상 출력:
```
=== 네이버 증권 스크래핑 테스트 ===

1. 거래량 급증 종목:
✅ 거래량 급증 종목 5개 수집 완료
  - 삼성전자 (71,000원 / +5.2%)
  ...
```

### 3. 전체 프로그램 실행
```bash
python updater.py
```

### 4. 특정 날짜로 테스트
```bash
python updater.py --date 2026-01-09
```

## ⚠️ 주의사항

1. **`.env` 파일은 절대 Git에 커밋하지 마세요!**
   - `.gitignore`에 이미 추가되어 있습니다
   - 실수로 커밋하면 즉시 Secrets 재발급 필요

2. **GitHub Actions는 `.env` 파일을 사용하지 않습니다**
   - GitHub Secrets에서 환경 변수를 가져옵니다
   - 로컬 개발용으로만 사용됩니다

3. **python-dotenv 미설치 시**
   - GitHub Actions에서는 자동으로 건너뜁니다
   - 로컬에서는 환경 변수를 직접 설정해야 합니다

## 🔧 문제 해결

### `.env` 파일이 로드되지 않을 때
```bash
# python-dotenv 설치 확인
pip install python-dotenv

# .env 파일 위치 확인 (프로젝트 루트에 있어야 함)
ls .env
```

### Credentials JSON 형식 오류
- JSON이 한 줄로 되어 있는지 확인
- 따옴표(`"`)가 올바르게 이스케이프되었는지 확인
- 줄바꿈(`\n`)이 `\\n`으로 이스케이프되었는지 확인
