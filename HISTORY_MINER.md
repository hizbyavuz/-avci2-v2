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


## Bias-aware validation layer (v0.3)

The raw archive remains untouched. `history_validation.py` adds a separate research/validation layer:
- fixed time split: discovery before 2025-09-01, validation from 2025-09-01 onward
- BTC regime tags (UP / SIDEWAYS / DOWN) using only data available at the case timestamp
- up to three controls matched within the same time split/regime using pre-event volume, volatility, drawdown and return
- hourly evidence split into EARLY (-72/-48/-24/-12h) and ONSET_RISK (-6/-3/-1h)
- a replication table that asks whether the direction found in discovery also appears in the untouched validation period
- explicit coverage flags rather than pretending missing evidence is solved

Known gaps remain explicit:
- the current broad miner is seeded from currently active Gate markets, so delisted/historical-market coverage is incomplete and survivorship bias is not yet fully removed
- historical holder concentration, float and wash-trade evidence is not available in this layer, so manipulation status remains UNKNOWN until a separate source is backfilled

No validation result changes live Avci thresholds automatically. A repeated pattern is only a research candidate until it also survives forward testing.


## Frozen matching and blind validation protocol (v0.3.1)

The matching design is now frozen as `match-spec-v1-frozen-20260924`.
For this version, each RISE case may receive up to three CONTROL matches. Controls must:
- be in the same discovery/validation split
- be within ±45 calendar days of the RISE case
- use the same BTC regime when regime information is available
- be ranked only with pre-event observables: 30-day return, 30-day drawdown, 30-day realized volatility, and log-transformed 30-day average quote volume
- use fixed distance scales: 20, 20, 5 and 2 respectively
- be tagged GOOD at distance <=0.75, OK at <=1.50, otherwise WEAK

This matching specification must not be tuned after examining the validation set. Any change to features, distance scales, windows, thresholds or regime logic requires a new version and a new untouched validation period.

Validation grades are fixed for this version:
- DIRECTION_ONLY_LOW_N: same direction, but fewer than 40 validation observations on either side
- DIRECTION_ONLY_WEAK: same direction, but discovery or validation effect magnitude is below 0.50
- CONSISTENT: same direction, at least 40 validation observations per side, and both effect magnitudes are at least 0.50
- STRONG: CONSISTENT plus validation effect magnitude at least 1.00
- FAILED_DIRECTION / INSUFFICIENT are kept explicitly

The Telegram report now prints discovery/validation regime counts so a result is not treated as general if the two time splits have materially different BTC-regime mixtures.

### Delisted-market backfill target

Current broad coverage is still seeded from active Gate markets, so survivorship bias remains a known limitation. The explicit next backfill target is a historical Gate market universe including delisted/suspended USDT pairs, with:
1. historical pair identity and listing/delisting dates,
2. recoverable daily/hourly candles,
3. the same event/control labels and pre-event features,
4. a separate coverage flag when historical data is incomplete.

Until that backfill exists, reports must keep the survivorship warning visible and must not describe the historical sample as the full Gate universe.


### Per-regime result display

For every globally STRONG or CONSISTENT feature shown in the Telegram validation section,
the report also displays its validation grade separately for BTC UP, SIDEWAYS and DOWN
regimes when enough data exists. This prevents a globally positive feature from being
mistaken for a regime-independent signal. Missing or weak regime evidence remains explicit
as WEAK / LOW_N / FAILED_DIRECTION / INSUFFICIENT rather than being hidden.
