# 구글 API 및 GitHub 설정 가이드

프로그램 자동화를 위해 꼭 필요한 3가지 설정 단계를 안내해 드립니다.

## 1. Google Cloud 서비스 계정 및 키(JSON) 만들기
이 과정은 프로그램이 사용자의 구글 계정 대신 시트와 캘린더에 접근할 수 있게 해주는 '열쇠'를 만드는 과정입니다.

1.  [Google Cloud Console](https://console.cloud.google.com/) 접속 및 로그인
2.  **프로젝트 선택/생성**: 상단 프로젝트 선택창에서 '새 프로젝트'를 만듭니다.
3.  **API 활성화**: 
    - 'API 및 서비스 > 라이브러리' 메뉴로 이동
    - `Google Sheets API`와 `Google Calendar API`를 각각 검색하여 **[사용]** 버튼을 누릅니다.
4.  **서비스 계정 생성**:
    - 'IAM 및 관리자 > 서비스 계정' 메뉴로 이동
    - **[서비스 계정 만들기]** 클릭 -> 이름 입력(예: stock-sync) 후 완료
5.  **키(Key) 생성 및 다운로드**:
    - 생성된 서비스 계정 이메일을 클릭하여 상세 페이지로 이동
    - **[키]** 탭 클릭 -> **[키 추가] -> [새 키 만들기]** 클릭
    - **JSON** 형식을 선택하고 **[만들기]**를 누르면 파일이 다운로드됩니다.
    - **이 파일의 내용 전체가 `GOOGLE_CREDENTIALS_JSON`이 됩니다.**

---

## 2. 구글 시트 공유 및 ID 확인

1.  **시트 공유**: 다운로드한 JSON 파일 내부의 `"client_email"` 주소(예: `stock-sync@...gserviceaccount.com`)를 복사합니다.
2.  사용하실 구글 시트로 가서 우측 상단 **[공유]** 버튼 클릭
3.  복사한 이메일 주소를 붙여넣고 권한을 **'편집자'**로 설정하여 공유합니다.
4.  **시트 ID 확인**: 시트 주소창의 URL에서 확인합니다.
    - 예: `https://docs.google.com/spreadsheets/d/1K39pYj8...HKUnThg/edit`
    - 여기서 `/d/` 와 `/edit` 사이의 문자열 `1K39pYj8...HKUnThg`가 **`SPREADSHEET_ID`**입니다.

---

## 3. GitHub Secrets 등록하기

작성해 드린 코드가 GitHub에서 돌기 위해 위 정보들을 안전하게 저장해야 합니다.

1.  GitHub 저장소(Repository) 페이지로 이동
2.  상단 메뉴의 **Settings** 클릭
3.  왼쪽 메뉴에서 **Secrets and variables > Actions** 클릭
4.  **[New repository secret]** 버튼을 눌러 다음 2개(또는 3개)를 추가합니다.
    - **Name**: `GOOGLE_CREDENTIALS_JSON`  
      **Value**: 다운로드한 JSON 파일 내용 전체를 복사해서 붙여넣기
    - **Name**: `SPREADSHEET_ID`  
      **Value**: 위에서 확인한 시트 고유 ID
    - **Name**: `CALENDAR_ID` (선택 사항)  
      **Value**: 본인의 이메일 주소를 입력하면 기본 캘린더에 저장됩니다.

---

## 4. 캘린더 권한 추가 (중요)
서비스 계정이 캘린더에 이벤트를 쓰려면 해당 캘린더도 서비스 계정 이메일과 공유되어야 합니다.
1.  [구글 캘린더](https://calendar.google.com/) 설정으로 이동
2.  사용할 캘린더의 **'특정 사용자와 공유'** 섹션에서 서비스 계정 이메일을 추가하고 **'이벤트 변경'** 권한을 부여합니다.
