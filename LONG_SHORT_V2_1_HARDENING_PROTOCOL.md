# Long/Short V2.1 Hardening Protocol

Status: CANDIDATE — TEST BEFORE PRODUCTION  
Date: 2026-10-06

V2.1 does not rewrite frozen V1.9 scoring or old V2 observations.

## Production engine
Only the continuation live watcher is production. The reversal worker remains in the repository for research/audit but is not executed in the production workflow.

## Cost-aware room
Minimum structural room is no longer a fixed 0.35%. It is:

max(0.90%, 3 × minimum round-trip execution cost)

With 5 bps fee + 10 bps minimum slippage per side, round-trip minimum cost is 30 bps and the room floor becomes 90 bps / 0.90%.

## Reward/risk gate
At the zone-edge trigger, Telegram's actual invalidation and Target 1 are used.
Target 1 must provide at least +1.0 net R after minimum round-trip costs.

## Validation identity
The levels shown to the user are the levels evaluated:
- entry reference / real delivery timestamp,
- invalidation / stop,
- Target 1,
- Target 2.

The old ATR paper model remains historical/audit data only and cannot be used as proof of V2.1 user-executable performance.

## Stage comparison
CLOSE_CONFIRMED and TRIGGERED are measured separately from the same real-time data path at 15m, 60m and 240m horizons.

This tests whether waiting for a retest adds edge or merely delays/loses good moves.

## Negative controls
Candidates that enter the live WATCH pool but leave without ever reaching CLOSE_CONFIRMED are retained as WATCH_NO_CONFIRM controls.

## Delivery timing
For delivered alerts, record condition time, Telegram delivery time, delay, and stale/missed-close flags.
Regular reports include p50/p95 delay.

## Cohorts
BINANCE_FUTURES_NATIVE remains the only confirmatory primary cohort.
Fallback cohorts (Bybit/Gate/other complete public derivatives bundles) are reported separately as secondary exploratory cohorts and are never silently pooled with native Binance evidence.

## Freeze rule
After production activation, V2.1 thresholds are frozen until at least 100 independent TRIGGERED episode clusters.
A critical implementation bug requires a new version identifier. Parameter tuning does not occur inside V2.1.
