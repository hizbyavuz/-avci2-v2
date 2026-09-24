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
