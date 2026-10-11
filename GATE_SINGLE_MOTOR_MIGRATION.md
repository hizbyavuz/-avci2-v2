# Gate-only LONG/SHORT migration (approved)

Status: MIGRATION IN PROGRESS, NOT DEPLOYED. No live orders.

## Target
- Gate USDT perpetual is the only signal and observation venue.
- BTC 4h RANGE must not block coin scans. BTC direction is context, not a hard gate.
- Determine LONG/SHORT independently from each coin's 4h trend; evaluate both sides, including against BTC direction.
- Keep support/resistance, volume confirmation, funding caution, execution costs, stop/target and minimum net R:R.
- No persistent SQLite, no volume, no backup bucket, no historical scan/signal archive. In-memory cooldown and open-signal state are allowed but reset on restart; no durable performance claims.
- Telegram signal and outcome messages, paper-only; never submit exchange orders.
- Remove old Binance LONG/SHORT and 451 probe services only after successful stop/removal confirmation; do not delete existing user data unless separately authorized.

## Required code edits to supplied gate_trader package
1. `gates.evaluate`: derive side from `trend(c4h, k)` rather than BTC trend; reject coin RANGE but not BTC RANGE; append BTC context to reasons.
2. `engine._eval_coin`: remove `coin_trend != btc_trend` early return.
3. `engine.scan`: remove `market == RANGE` early bypass.
4. Replace `ledger` SQLite with an in-memory signal registry, or remove tracking entirely if no memory is desired. Never claim persisted win rates.
5. `daemon`: no DB startup, no persistent daily summary, no volume mount.
6. Validate Gate API and Telegram integration and run all tests before production.

Existing Gate Trader ZIP is an external supplied artifact, not yet committed into this repository. This document is not a live deployment.