# 2026년 전략 로드맵 & 방향성

**날짜**: 2026-01-14
**상태**: 승인됨 & 진행 중

## 1. 요약 (Executive Summary)

이 문서는 **Google Sheet Stock Updater** 프로젝트의 전략적 방향을 확정합니다. 핵심 결정 사항은 **구글 시트(Google Sheets)를 UI(모바일/데스크탑)** 로 유지하면서, **파이썬 백엔드(Python Backend)** 를 고성능 모듈형 시스템으로 발전시키는 것입니다.

### 핵심 철학: "하이브리드 아키텍처"
*   **프론트엔드 (UI)**: 구글 시트 (Google Sheets)
    *   **이유**: 모바일 접근성이 가장 뛰어나고, 인터페이스가 익숙하며, 별도의 앱 유지보수 비용이 없음.
    *   **역할**: 설정 입력(Configuration) 및 대시보드 출력(Dashboard).
    *   **실행**: 계속 사용함. 드롭다운/유효성 검사를 통해 UX 개선.
*   **백엔드 (Backend)**: 파이썬 (GitHub Actions)
    *   **이유**: 강력한 데이터 처리 능력, 유연한 외부 API 연동.
    *   **역할**: 데이터 수집, 분석, 로직 실행 등 "무거운 작업" 담당.
    *   **실행**: 단일 스크립트(`updater.py`) 구조에서 모듈형 아키텍처로 리팩토링 및 비동기(Async) 도입.

---

## 2. 주요 목표

1.  **안전성 (Reliability)**: 코드를 모듈화하여 "스파게티 코드" 위험 제거.
2.  **성능 (Performance)**: 비동기(Async I/O)를 도입하여 실행 시간을 예상 15분에서 3분 미만으로 단축.
3.  **유지보수성 (Maintainability)**: 외부 API(예: 네이버 HTML 구조) 변경 시 해당 어댑터 파일만 수정하면 되도록 분리.
4.  **AI 협업용이성 (AI Compatibility)**: AI 어시스턴트가 프로젝트 맥락을 쉽게 이해하고 정확한 코드를 짤 수 있도록 구조화.

---

## 3. 단계별 실행 계획 (Phased Implementation Plan)

우리는 시스템을 망가뜨리지 않기 위해 안전하게 단계별로 진행합니다.

### 1단계: 기반 마련 & 문서화 (현재 단계)
*   **목표**: 기존 코드를 건드리지 않고 대공사를 위한 기반 다지기.
*   **할 일**:
    *   [x] Perplexity 제안 분석 완료.
    *   [x] `docs/PROJECT_CONTEXT.md` 작성 (AI를 위한 "설명서").
    *   [x] 현재 아키텍처 vs 목표 아키텍처 문서화.

### 2단계: 모듈화 (리팩토링)
*   **목표**: 1700줄이 넘는 `updater.py`를 작고 논리적인 파일들로 쪼개기. **(로직 변경 없음)**
*   **할 일**:
    *   `src/` 디렉토리 구조 생성.
    *   `adapters/google_sheets.py` 분리 (구글 시트 입출력 담당).
    *   `adapters/naver_finance.py` 분리 (크롤링 담당).
    *   `domain/models.py` 분리 (데이터 구조 정의).
    *   이 모듈들을 조율하는 `main.py` 생성.

### 3단계: 성능 최적화 (비동기 도입)
*   **목표**: 순차적(Sequential) 처리 방식을 병렬(Parallel) 처리로 전환.
*   **할 일**:
    *   `asyncio` 및 `aiohttp` 라이브러리 도입.
    *   `get_stock_data`의 반복문을 `fetch_all_stocks_async()`로 대체.
    *   구글 시트 API 호출을 `batchGet`, `batchUpdate`로 최적화하여 할당량 절약.

### 4단계: 안정화 (테스트)
*   **목표**: 변경 사항이 시스템을 망가뜨리지 않도록 보장.
*   **할 일**:
    *   `pytest` 도입.
    *   연결 확인용 테스트 작성 (네이버 접속 되는지, 시트 쓰기 되는지).

---

## 4. 목표 아키텍처 다이어그램

```mermaid
graph TD
    User([사용자 (모바일)]) -->|설정 변경| GSheets[구글 시트 (Config 탭)]
    
    subgraph "파이썬 백엔드 (GitHub Actions)"
        Orchestrator[main.py / 파이프라인]
        
        subgraph Adapters
            GSheetAdapter[구글 시트 어댑터]
            NaverAdapter[네이버 증권 어댑터]
            YahooAdapter[야후/코인 어댑터]
        end
        
        subgraph Domain
            Logic[비즈니스 로직]
            Models[데이터 모델]
        end
    end
    
    GSheets -->|설정 읽기 (배치)| GSheetAdapter
    GSheetAdapter --> Orchestrator
    
    Orchestrator -->|비동기 수집| NaverAdapter
    Orchestrator -->|비동기 수집| YahooAdapter
    
    NaverAdapter -->|원천 데이터| Logic
    YahooAdapter -->|원천 데이터| Logic
    
    Logic -->|가공된 데이터| Orchestrator
    Orchestrator -->|결과 쓰기 (배치)| GSheetAdapter
    GSheetAdapter -->|업데이트| GSheets
```
