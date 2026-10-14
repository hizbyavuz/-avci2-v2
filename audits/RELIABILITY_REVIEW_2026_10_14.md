# Reliability and measurement review — 2026-10-14

Baseline deployment: 986e182; Railway long-short-motor, production.

Verified findings:
- Futures ticker/derivative/candle workers alive. No authorized core entries in inspected scans.
- Early delivery ACK count increasing; one permanent card per symbol causes new episodes to edit old messages instead of notifying again.
- Optional V4 reclaim failed on SQLite sidecars. Disk 93.48% used, ~289 MB free. V4 DB ~1.946 GB. Remote backups ~58.28 GB, not fully reclaimed.
- Current 24h message-level 30m early counts: 83 RIGHT, 96 WRONG, 6 FLAT, 4 pending. These include updates, so are not independent trades or net profit.
- Gate Trader has no current deployment; it is not silently restarted here.

Changes:
- Per-episode Telegram card identity: new episodes send; subsequent same-episode updates edit. Existing uncertain-send protection retained.
- LS_CANDIDATE_PRIORITY_V2 separates scheduling from suppressing early messages. At most six existing scan slots; no liquidity/data/entry bypass.
- Early messages describe 1h/4h direction agreement, 24h range position, and recent hourly volume. Missing context remains explicit, not favorable by default.
- Hourly context scorecard counts first EARLY transition once per episode, separates delivered/control observations and config hashes, uses only event-time context, reports same-endpoint always-long/always-short and fair-random expected direction; costs are declared sensitivity proxies, not executable PnL.
- V4 reclaim closes SQLite explicitly, checkpoints via SQLite, archives CURRENT bytes by content hash, reads remote bytes back and verifies, refuses deletion if local file changes/reopens or sidecars return.
- Older completed redundant backup sets removed only after a complete current set is validated; recent incomplete uploads protected until 24h expiry.

No predictive edge claim, no fitted weights, no change to frozen entry thresholds, no automated orders. Correlated episodes invalidate treating nominal Wilson intervals as proof. Main next validation is prospective, cost-aware target/stop outcomes over distinct market regimes. A descriptive context label is not a validated production veto.

Research references:
- https://www.sqlite.org/wal.html
- https://www.sqlite.org/c3ref/wal_checkpoint_v2.html
- https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf

Validation: 447 unit tests passed; git diff --check clean. Live deployment verification follows separately.
