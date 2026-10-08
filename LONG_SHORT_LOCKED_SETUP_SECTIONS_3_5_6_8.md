# LONG/SHORT — Locked setup + ACTIVE outcome engine (sections 3, 5, 6, 8)

2026-10-08. **Scope: no new indicators, no score/threshold change, no real orders, no modification of the user's Section 2 measurement figures.**

## Modules and integration

- `long_short_setup_lifecycle.py`: `setups` (frozen levels/config SHA), `setup_events` (append-only), `setup_outcomes`, legal state transitions, one live idea/symbol, opposite-direction closure, same-direction immutable lock, delivered ACTIVE and 2h/15m independence identifiers.
- `long_short_setup_bridge.py`: attaches V3.1 live-pool WATCH/CLOSE_CONFIRMED/RETESTING/INVALIDATED observations to the new ledger. Radar stays out. The current V3.1 `TRIGGERED` alert is **not** automatically classified as ACTIVE.
- `long_short_setup_outcomes.py`: first 1m STOP/TP1/TIMEOUT barrier, pessimistic stop-first, gap stop at first executable opening price, after-cost return/net R, 2x cost stress, pre-exit MFE/MAE, same-venue BTC excess when available, immutable DATA_GAP/NULL outcome, distinct independent wave averages.
- `long_short_live_pool.py`: observational bridge enabled by `LS_SETUP_LEDGER_OBSERVE=1`; same-direction setup-type rescans no longer move frozen levels; event logging and live signal thresholds remain frozen.
- `long_short_outcome_tracker_v21.py`: records *new* delivered ACTIVE primary results separately and explicitly tags old V3.1 records as legacy proxy audit; no historical results are rewritten.

## Important incompatibility, intentionally NOT hidden

Frozen V3.1 currently delivers Telegram at `TRIGGERED` **after** the retest/acceptance. Section 3 requires ACTIVE when an executable price actually exists **inside** the frozen retest band, **after** a final Telegram delivery. Mapping a later post-retest legacy alert into an earlier in-band entry would manufacture a fill before the user had an alert.

Consequently the adapter writes confirmed/retest observations and does not convert these old TRIGGERED alerts to ACTIVE. **Without a future, separately authorized user-facing alert/entry timing change (Section 4, out of scope), actual V3.1 signals will not populate the new ACTIVE primary performance set.** This is intentional fail-closed behavior, not an assertion of profitable trading.

The legacy `delivered_signal_outcomes` table is kept for longitudinal auditing and never merged into `setup_outcomes`.

## Tests

- `tests/test_long_short_setup_lifecycle.py` — 10 tests: immutable levels/metadata, flips, expiry, legal entry/delivery, stop, event append-only.
- `tests/test_long_short_setup_outcomes.py` — 14 tests: first target, stop-first, both gap directions, fixed timeout, data gap, negative 2x costs, mature horizon, entry/lookahead, delivered-only, independence and actual venue fetch contract.
- `tests/test_long_short_setup_bridge.py` — 6 tests: radar isolation, current V3 legacy alert NOT equal to ACTIVE, exact Telegram levels, watch vs triggered, expiration, no execution endpoints in setup modules.
- Standard Long Short CI: https://github.com/hizbyavuz/-avci2-v2/actions/runs/37709275304 **passed** after path tests and schema integration.
- Real DB copied from last successful V3.1 artifacts, not production mutated: https://github.com/hizbyavuz/-avci2-v2/actions/runs/37709223944 **passed**. Snapshot: 746 unchanged historical V3 event rows, 1 unchanged SENT TRIGGERED, 11 radar watchlist items, 0 actionable, 0 ACTIVE and 0 phantom outcomes; integrity `ok`. The same isolated test creates one synthetic WATCH to validate the schema/insert path, never passed off as an actual market signal.

## Deployment

This remains a **draft PR**, not merged to main. The original frozen V3.1 live system continues independently. Do not activate a new Telegram/ACTIVE policy as part of Sections 3/5/6/8, because the user explicitly excluded Section 4. Do not present an empty primary outcome cohort as proof of trading quality. Do not apply scoring or threshold optimization.
