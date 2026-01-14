# Implementation Plan - Update Schedule to 09:10 KST

The user wants to change the "Morning Briefing" schedule from 08:01 KST to 09:10 KST.

## Rationale
*   **Current**: `1 23 * * *` (UTC) -> 08:01 KST.
*   **Target**: 09:10 KST -> **00:10 UTC**.
*   **Action**: Update cron expression and the conditional logic that detects this schedule.

## Proposed Changes

### `.github/workflows/daily_sync.yml`

#### [MODIFY] Update Schedule and Mode Logic
```yaml
    # NXT 한주 소식 시작 (모닝브리핑): 09:10 KST = 00:10 UTC
-    - cron: '1 23 * * *'
+    - cron: '10 0 * * *'
```

```bash
-            if [ "${{ github.event.schedule }}" == "1 23 * * *" ]; then MODE="MORNING";
+            if [ "${{ github.event.schedule }}" == "10 0 * * *" ]; then MODE="MORNING";
```

## Verification Plan

### Automated
*   There is no way to "force" a cron event immediately in GitHub Actions without waiting.
*   However, we can verify the syntax by committing and ensuring the Action parses correctly (no syntax error).

### Manual
*   The user will need to wait for 09:10 KST tomorrow to verify the actual trigger.
