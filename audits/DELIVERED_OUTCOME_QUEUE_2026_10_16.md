# Delivered-message measurement repair

Status: prepared and tested; production validation is pending deployment.

## Evidence and cause

Railway production `long-short-motor` ran commit
`360d29aebe530c0b578fecc454f78537ee6b4777` during investigation.
At 2026-10-16 13:22:57 UTC the V4.1 delivered-WATCH cohort contained
391 records: 387 were due for a 15-minute result but had no outcome row;
4 were not yet due. READY outcomes were largely available.
The 13:59 UTC runtime report contained 29,892 rejected assessments in its
last-24-hour window, alongside 546 delivered assessments.

The existing resolver selects at most 90 assessments per scan, prioritizes
DELIVERED_READY, then sorts every other stage by oldest ID. Delivered-WATCH
therefore competes with historical rejected and unconfirmed observations,
including hourly retries of unavailable paths. A deterministic regression
fixture reproduces the starvation behind 200 rejected assessments while
READY is processed. This proves the queue defect, not that every historical
missing candle can be recovered.

## Repair

- Run a separate bounded measurement process in the existing container.
- Select only acknowledged DELIVERED_WATCH and DELIVERED_READY records.
- Preserve the existing 15/60/180-minute labeling functions and reference
  costs captured at message time. Add a separately identified 30-minute
  observational horizon; 180 minutes remains the primary endpoint.
- Process at most 120 due horizon jobs per 10-second cycle. Retry unresolved
  data after an hour, without labeling it a win or a loss.
- Read native candle data without network requests or exchange orders.
- Commit individual small writes. Protect resolved/boundary-ambiguous rows
  against replacement, including concurrent completion by the original worker.
- Emit per-message results with saved signal-time features. Replay at most
  2,000 completed results from the last 48 hours on startup, explicitly marked;
  consumers must deduplicate by `(assessment_id, horizon)`.
- Emit missing, pending, unresolved and resolved counts per stage/horizon.

No signal source file, admission threshold, notification function, old outcome
label, or Telegram destination is changed. No additional Railway service is
created. The existing original resolver and control observations remain intact.

Verified unchanged identities:

- Strategy: `b3154047e7fc7631de651a4431bd478e61b54098ebb63faed6bd216cf2bc762a`
- Config: `d9fdffbee356ce8984080979c33f38948ae46409dadc1b284520eb9558eae557`

## Validation

Run chart tests with `python -m unittest discover -s tests -p 'test_chart*.py'`
and runtime integration with
`PYTHONPATH=tests python -m unittest test_runtime_cleanup.RuntimeTests`.
The development environment additionally needs the existing requests dependency.

Covered risks: old-queue starvation; immutable assessments; missing-path
retry/recovery; verified Futures sources; horizon maturity; legacy endpoint
compatibility; historical cost preservation; partial-minute ambiguity;
bounded batches and startup replay; preserved signal-time features; concurrent
completion; restart idempotency; daemon worker integration.

## Required live checks after approval

1. Observe deployment SUCCESS for this revision, then worker START.
2. Read CHART_DELIVERY_OUTCOME_PROGRESS and verify missing_due declines for
   delivered-WATCH at each horizon, without relabeling data failures as losses.
3. Verify chart scanning and Telegram dispatch remain healthy and strategy/config
   identities remain the values above.
4. Rebuild matched winner/loser feature comparisons from CHART_DELIVERY_MEASURED,
   keeping versions, stages and horizons separate and deduplicating replay.

This repairs measurement coverage; it is not evidence of profitable signals.
Some historical paths may be absent from retained candles and remain unresolved.
