# Long/Short V3 — Final Forward-Test Design

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

## Freeze
No thresholds or logic changes for the first 7–10 calendar days except critical data-integrity/runtime bugs, which require a new version tag and intervention log.
