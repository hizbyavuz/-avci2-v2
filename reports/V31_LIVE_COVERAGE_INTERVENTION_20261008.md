# V3.1 runtime observation intervention — 2026-10-08

**Change class:** critical live-data observation/continuity bug fix, NOT signal decision, risk gate, scoring or fee threshold.

## Evidence
Completed 2026-10-07 production live run 37703861245 logged:
- Run started 2026-10-07 23:43:46 UTC
- It terminated its planned observation window at 23:45:04 UTC
- Intended LS_LIVE_RUN_SECONDS=330, actual configured window was approximately 78 seconds
- Reason: prior `main()` chose `min(start+330, next_5m_boundary+4s)`.

The next analyst step ran after outcome tracking, so an observation gap could occur between live workers. A successful GitHub Actions job was not proof of continuous market coverage.

## Repair
- Watch window always lasts the requested `LS_LIVE_RUN_SECONDS` (330 seconds in live config).
- Still poll every 15 seconds and use only closed candles for confirmation.
- Expand live runner job timeout to 12 minutes to accommodate extended observation plus outcome tracking.
- Tests include starts before and after the five-minute boundary.
- Same eligibility, LONG/SHORT scores, structure quality threshold, trigger levels, TP/SL, entry-cost gate and Telegram rules. No automatic trading.

## Experimental comparability
- This changes the observation/exposure time of future signals, so pre-fix versus post-fix results are **separate cohorts**.
- Existing recorded events must remain unchanged and must not be relabeled as if they had received 330-second coverage.
- Do not claim model profitability from a longer watcher or infer signals missed in previous blind windows without replay.
- Compare the observed number of full monitoring seconds and number of TRIGGERED alerts per operational regime.
- If GitHub Actions still causes unavoidable gaps, the durable solution is a persistent WebSocket/worker service, not further reduction in signal quality rules.

## Verification
PR #29 CI run https://github.com/hizbyavuz/-avci2-v2/actions/runs/37704394089 passed all tests and derivatives smoke prior to merge.
