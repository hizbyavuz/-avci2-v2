# Gate Avci History Miner

History Miner runs beside Gate Avci V5 and does not change frozen candidate rules.

## Goal
Reconstruct a contract-based token timeline: earliest observable pool, market snapshots, price/volume/liquidity milestones, later candle history, holder/wallet evolution, CEX listing events, and post-listing outcomes.

## v0.1
The first version deliberately starts cheap:
- seeds exact Gate contracts from `avci2.db`
- records pool creation and market snapshots from GeckoTerminal
- persists queue/checkpoints
- records API usage
- works in small hourly batches

No API key is required for v0.1. Historical candle, holder-history, wallet-flow and listing backfill are subsequent layers. Missing historical evidence must stay explicitly missing; it must never be invented.

The database is `history_miner.db`. Each future layer should append evidence keyed by `network_id + contract`.


## Broad event-mining layer (v0.2)

The main research path is intentionally broad and shallow first.

For each real Gate USDT market with enough liquidity/history, the miner now:
- downloads up to 730 daily candles
- marks independent 60-day rise events when future max return reaches at least +20%
- separately records whether that same event reached +50% or +100%
- creates non-event controls from periods whose next 60 days stay below +10%
- computes only pre-event features: 7/30/90-day return, 30-day drawdown, volume acceleration, realized volatility, range compression, green-day ratio and distance from 90-day high
- stores raw daily bars so new features can be added later without re-downloading everything

The purpose is not to assume one indicator causes pumps. The purpose is to compare thousands of rise events against controls and discover which combinations actually separate them.

Deep wallet/holder work remains a second-stage drill-down only after broad features show repeatable separation.

Important: event labels use future data only as the outcome. Every feature is calculated strictly from data available on or before the event timestamp. This avoids look-ahead leakage in later modelling.
