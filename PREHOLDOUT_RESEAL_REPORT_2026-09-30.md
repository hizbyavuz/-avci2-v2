# Pre-Holdout Reseal Report — 2026-09-30

## Decision
The previous baseline commit `9d3c428c63b1282e97c7a22a3925d3d679a2c8e2` is **superseded** for the 2026-10-07 prospective holdout.

Reason: the audit found real semantic drift after the old seal. Calling all drift "operational" would be inaccurate.

The new prospective baseline is:

`dadada65ed6cb7a16c6ae7ca3d0c7665db55ae5a`

The planned holdout start remains **2026-10-07T00:00:00Z** because the reseal occurs before the holdout and the final contract is fixed before the protected period begins.

## Material findings from old baseline -> new baseline

### Selection-semantic changes — MATERIAL
1. **binance_scanner.py**
   - Old behavior: assets with 90-day floor gain >= 50% were hard-excluded.
   - New behavior: they remain eligible and receive a recent-runner context flag.
   - Classification: **selection-semantic change**.
   - Consequence: old baseline cannot be defended as byte/selection equivalent.

2. **scanner.py / Gate validation cohort**
   - Validation is restricted to candidates that actually satisfy at least one frozen V5 ruleset before controls/events are written.
   - Classification: **validation-membership semantic change**.
   - Consequence: old and new validation cohorts are not identical.

3. **gate_weighted_discovery.py**
   - Security-review count, very-new-pool age scoring and safe-discovery output filters changed.
   - Classification: **observational discovery semantic change**, not frozen V5 candidate membership.
   - Consequence: it is excluded from the primary confirmatory claim but is still treated transparently as changed research behavior.

### Non-selection / integrity-oriented changes
- **gate_evidence_research.py** adds as-of guards and persistence/re-ignition observational evidence; History Miner is explicitly prevented from increasing the main evidence count.
- **binance_evidence_research.py** removes Winner Anatomy from the live evidence count, reducing outcome-derived contamination.
- **gate_spot_bridge.py** adds bounded future-clock-skew handling.
- **research_validation_layer.py** centralizes windows and excludes malformed Gate candidate rows without frozen rulesets.
- **research_decision_discipline.py** centralizes the holdout time and excludes malformed Gate candidate rows.
- SQL placeholder fixes in activation/governance modules are implementation corrections.

## Readiness historical-edge issue
`trade_readiness_layer.py` uses historical candidate-vs-control outcomes as human-facing readiness evidence. This is downstream/adaptive and therefore:
- MUST NOT redefine the frozen confirmatory CANDIDATE cohort;
- MUST NOT be used as the primary prospective endpoint;
- Telegram/PAPER_ELIGIBLE outcomes are secondary only.

## New Launch
The 2026-09-30 New Launch liquidity-collapse hardening is **not part of the Binance primary holdout**. New Launch is an isolated observational cohort and cannot satisfy, rescue or contaminate the Binance primary claim unless code is later coupled into protected primary selection.

## Primary engine
The 2026-10-07 confirmatory primary engine is **BINANCE**.

Gate is a separate secondary engine for this holdout.
New Launch is exploratory only.

## BTC-DOWN definition
BTC-DOWN is determined at scan/signal time from Binance BTCUSDT spot 24h ticker `priceChangePercent <= -2.0%`.

No future candle, end-of-day classification or retroactive regime relabeling is permitted.

## Inconclusive is allowed
If enough BTC-DOWN episodes do not occur within the 120-day window, or execution evidence/governance coverage is insufficient, the result is **INCONCLUSIVE**. Other regimes, Gate or New Launch may not replace the primary hypothesis post hoc.

## Baseline rule
From the new baseline forward:
- no protected selection/feature/label/execution/confirmatory-analysis semantics may change from holdout outcomes;
- a required semantic change creates a new version and a new prospective start date;
- contract hash and protected-file byte drift are checked by CI.
