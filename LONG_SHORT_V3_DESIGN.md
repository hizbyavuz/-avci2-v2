# Long/Short V3.1 — Final Forward-Test Design

This version is intentionally smaller than the previous rule engine.

## Core decision
Discovery asks **whether expansion is forming**, then the setup engine asks **which direction and which setup**. Additive LONG/SHORT scores are retained only as shadow diagnostics and cannot authorize a signal.

## Live setups
- **BREAKOUT**: balance/compression -> key level -> close/acceptance -> FAST path or controlled retest path.
- **PULLBACK**: established 1h/15m impulse -> controlled retrace -> micro-structure restart.
- **REVERSAL** remains shadow-only until independent evidence exists.

## Hard veto families
Data health; executable cost/net-R; pre-signal ATR extension; nearby major HTF obstacle; BTC volatility shock; continuation with OI falling in the same price direction (cover/liquidation); extreme funding/crowding; fake breakout/sweep.

## Flow
Use real venue-tagged data only. Spot delta/CVD proxy may use real Binance Spot taker-buy quote volume. Synthetic 50/50 normalized external candles must never be called CVD. Order-book snapshots are diagnostic only.

## Timeframes
5m/15m/1h drive setup and direction. 4h/1d are obstacle/extension context only.

## Validation
Telegram and outcome evaluation consume the same immutable signal levels. WATCH remains a separate control group. Results are grouped by correlated market-wave cluster. 100 independent clusters is only a health check, not proof of edge.

## Deliberately not in V3.1
To avoid rebuilding a rule-engine monster, V3.1 does **not** make these live decision inputs: sector scoring, breadth scoring, news NLP, order-book directional snapshots, order-book replenishment/cancellation models, direct reversal signals, per-coin tuned thresholds, or a large OI×funding×CVD combination table. They may be logged/researched later, not added during the freeze.

## Freeze
V3.1 starts from fresh analyst/live/notify databases. No thresholds or signal-logic changes for the first 7–10 calendar days except critical data-integrity/runtime bugs; any such fix must create a new version and restart the clean sample.
