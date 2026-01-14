# Summary of Perplexity AI Recommendations

This document summarizes the insights and recommendations found in the files under `docs/fromPerplexcity.ai`. These documents provide a comprehensive guide for optimizing, structuring, and maintaining the Google Sheet Stock Updater project.

## 1. Performance Optimization (`구글독스 시트를 읽어서1.md`)

**Goal:** Improve the execution speed of the GitHub Actions script (currently slow).

*   **Batch Requests:**
    *   Replace individual API calls with `spreadsheets.values.batchGet` and `batchUpdate`.
    *   Reduces HTTP overhead and helps stay within API rate limits.
*   **Asynchronous Processing:**
    *   Use `asyncio` and `aiohttp` for external API calls (Naver Finance, etc.).
    *   Parallelize data fetching instead of sequential processing (estimated 7x speedup).
*   **Threading:**
    *   Use `ThreadPoolExecutor` for blocking I/O operations where async isn't available (e.g., standard Google API client).
*   **Caching:**
    *   **GitHub Actions Cache:** Cache Python dependencies (`pip`).
    *   **Data Caching:** Cache static data (stock lists) or slowly changing data to avoid repetitive fetches.
*   **Rate Limit Management:**
    *   Implement exponential backoff for retries on 429 errors.

## 2. Testing & Quality Assurance (`구글독스시트를읽어서2.md`)

**Goal:** Ensure data accuracy and safe code evolution.

*   **Project Structure:** Recommended hexagonal architecture separating Adapters (API clients), Domain (Business Logic), and Services.
*   **Testing Layers:**
    *   **Unit Tests:** Mock external APIs to test logic quickly.
    *   **Integration Tests:** Verify interaction with actual APIs (subset of data).
    *   **Performance Tests:** Use `pytest-benchmark` to track execution time and detect regressions.
*   **CI/CD Pipeline:**
    *   Separate workflows for Pull Requests (Test) and Cron Jobs (Production Run).
    *   Monitor performance metrics over time.

## 3. Architecture: Google Sheets as UI (`구글독스시트를읽어서3.md`)

**Goal:** Maintain mobile-friendly control while optimizing backend performance.

*   **Hybrid Concept:** Keep Google Sheets as the "Frontend" for mobile configuration, use Python as the "Backend" for heavy lifting.
*   **Sheet Structure:**
    *   **Config Tab:** User inputs settings (Stock codes, enabled alerts) via dropdowns/checkboxes.
    *   **Dashboard Tab:** Python writes high-level summaries here for quick mobile viewing.
*   **Workflow:**
    1.  **Mobile:** User updates 'Config' sheet.
    2.  **Backend:** Python script reads 'Config' (Batch Get).
    3.  **Backend:** Python processes data (Async/Parallel).
    4.  **Backend:** Python writes results to 'Stocks'/'Dashboard' sheets (Batch Update).
    5.  **Mobile:** User views results in Sheets app.

## 4. AI Collaboration Strategy (`구글독스시트를읽어서4.md`)

**Goal:** effectively communicate project context to AI assistants.

*   **Context Documentation:** Create structured markdown files to "onboard" the AI.
    *   `PROJECT_CONTEXT.md`: High-level overview and goals.
    *   `ARCHITECTURE.md`: System design and data flow.
    *   `OPTIMIZATION_GUIDE.md`: Specific technical strategies being used.
*   **Workflow:** Provide these context files at the start of an AI session to ensure the assistant understands the "Hybrid Architecture" and performance goals.
