# V3.1 as-built freeze and validation audit (2026-10-08)

**Status: OBSERVATIONAL PROPOSAL.** Do not merge into the forward test before review. This change does not alter live scoring thresholds, stop/TP, execution gates, or Telegram routing. Automatic real-money orders remain OFF.

## Current implementation is not the same as frozen design document

The frozen file LONG_SHORT_V3_FROZEN_CONFIG.json describes an earlier intended model, NOT every aspect of the active production code. Do not rewrite the historic design retroactively.

| Topic | Frozen document | Current V3.1 code | Treatment |
| --- | --- | --- | --- |
| Direction | Hard gates, scores shadow-only | A weighted LONG/SHORT direction model with minimum 42 score and 12-point edge | Log as-built and evaluate alternate rules in a future version |
| 4h and 1d | Context/obstacles, not direction | Analyst awards 4h trend +12, 1h +10, 15m +6 | No live change; measure duplicate features later |
| Spot delta | Required in V3 core | Soft bonus of at most +6 if available | No live change; compare frozen V3 model later |
| External candles | Authentic order flow expected | Bybit/Gate fallback candle normalizer fills taker-buy quote as 50% of total volume | Treat that candle-derived flow as synthetic, not actual taker buy flow |
| Execution/outcome venue | Need a common venue | Outcome tracker evaluates Binance Spot 1m after possibly external perpetual signal | Flag as a proxy, never same-venue realized profit |
| Slippage | Per-execution estimate | $250 book depth proxy with 10 bps per-side floor | Quantify by coin liquidity; real fills may differ |
| Delivery | Prompt entry needed | GitHub Actions scheduler + condition/sent timestamps | Track delay distribution; do not assume exact 15s responsiveness |

Scores are NOT calibrated probabilities.

## New read-only validation observer

New script: long_short_validation_observer_v31.py

- Reads only already recorded, delivered TRIGGERED outcome events and measured 15/60/180 minute horizons.
- Compares the live direction with seeded RANDOM_DIRECTION and prior-15-closed-1m MOMENTUM_15M directions.
- Baselines have exactly the same entry price, the original stop and TP distances (mirrored when direction differs), first-barrier execution, and the same fee/slippage assumptions.
- The random comparator is deterministic per event, not fitted to realized prices. Momentum never uses a candle closing after the signal.
- Independently counts full five-field single-venue derivative packages and four-field ready core packages; fallback V3_CORE_FULL must NOT be called a full five-field package.
- Segments event source/quality and flags all Binance Spot 1m results as PROXY, not true perpetual venue-matched results.
- Reports matched-pair model-minus-baseline net return, distinct market-wave clusters, and 95% cluster bootstrap intervals.
- Preserves errors and insufficient-data cases separately; never manufactures a losing outcome.
- Runs incrementally (up to eight events per run) in separate observer tables in the existing live SQLite DB.

Proposed non-decision provenance in long_short_live_pool.py: annotate each newly emitted event with frozen JSON file SHA256, Git commit SHA, engine version and source/provenance. Old events remain explicitly marked HISTORICAL_MISSING, not backfilled by guesswork.

## Installation/review sequence

1. Finish ordinary V3.1 outcome tracking first.
2. Run observer with the same restored LS_LIVE_DB, e.g. python long_short_validation_observer_v31.py --max-events 8.
3. Preserve the live DB artifact so new observer-only tables survive the next runner.
4. Review source-health ratio, paired baseline comparisons, number of independent clusters, issues and delivery distribution.
5. Merge only after explicit approval; keep thresholds frozen and start a separately versioned V3.3 if direction rules are redesigned.

## Limitations to resolve after frozen comparison

- No same-venue perpetual execution prices in present outcome tracker.
- Binance Futures 451 fallback is a structural data-quality limitation.
- Analyst uses global DATA_MODE across parallel workers; per-chart source provenance cannot yet be guaranteed without an isolated fetch-router change.
- Correlated samples reduce effective size; confidence intervals with few clusters are inconclusive.
- Score components overlap; run out-of-sample ablations only after sufficient independent outcomes.
