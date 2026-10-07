# V3.1 FIRST REAL-RECORD AUDIT (2026-10-08, 02:42 TRT)

**Source:** Main-branch completed Live Pool run [37703387603](https://github.com/hizbyavuz/-avci2-v2/actions/runs/37703387603), GitHub artifact `long-short-v31-live-pool-state`. Independently inspected by read-only branch workflow [37703754529](https://github.com/hizbyavuz/-avci2-v2/actions/runs/37703754529). Main trading rules, alerts and records were not changed. All returns below are *Binance Spot 1m proxy outcomes* after estimated fees and slippage, not verified same-venue Futures fills.

## Database inventory

- SQLite integrity: OK
- Raw state events: 683, NOT 683 trade recommendations
- Watch episodes: 543
- Live watch rows: 22 (WATCH 16, APPROACHING 5, INVALIDATED 1)
- Sent TRIGGERED actionable trade alerts: **1**
- Historic delivered CLOSE_CONFIRMED alerts: **3**, which are **NOT** counted as trade-confirmed TRIGGERED signals
- Event outcomes: 8 rows = four distinct message-stage events at **15 and 60 minutes** each

## Stage funnel (state transitions, NOT mutually independent signals)

| State to | Count | Telegram status |
| --- | ---: | --- |
| APPROACHING | 396 | NOT_APPLICABLE |
| WATCH | 265 | NOT_APPLICABLE |
| EARLY:PENDING | 7 | unmessaged |
| EARLY:CHASE | 4 | unmessaged |
| CLOSE_CONFIRMED | 3 | SENT (prior messaging policy) |
| INVALIDATED | 4 | 2 historical SENT, 2 silent |
| EARLY:BROKEN | 1 | unmessaged |
| EARLY:EARLY_SHORT | 1 | unmessaged |
| EXECUTION_BLOCKED | 1 | SHADOW |
| TRIGGERED | **1** | SENT |

**Watch episode end reasons:** RESET_DIRECTION_OR_LEVEL 289, DROPPED_FROM_WATCHLIST 154, WATCHLIST_GRACE_EXPIRED 73, ACTIVE 22, UPGRADE_RADAR_TO_ACTIONABLE 2, TRIGGER_EXECUTION_GATE 1, RESET_SETUP_TYPE_CHANGE 1, RESET_ACTIONABLE_DIRECTION_FLIP 1. These are descriptive counts, NOT proven bugs. Investigate whether repeated level changes or short-lived watchlist admission degrade opportunities; don't modify frozen conditions based on counts alone.

## All observed user-facing stage outcomes

| Coin | Stage | Side | Delivery delay | Net 15m | Net 60m | 60m barrier |
| --- | --- | --- | ---: | ---: | ---: | --- |
| UNIUSDT | CLOSE_CONFIRMED | SHORT | 260.5 sec | -0.4033% | -1.3153% | STOP |
| XRPUSDT | CLOSE_CONFIRMED | SHORT | 195.5 sec | -0.6180% | -0.7165% | TIMEOUT |
| SOLUSDT | CLOSE_CONFIRMED | SHORT | 217.3 sec | -0.4382% | -0.6534% | TIMEOUT |
| SOLUSDT | TRIGGERED | SHORT | 1.5 sec | -0.2568% | -0.5585% | TIMEOUT |

No observed event hit TP1. Three CLOSE_CONFIRMED messages were under the *older messaging policy*. In the new trade-only policy only TRIGGERED counts as a delivered trade alert. All entries share SPOT_PLUS_GATE source cohort.

## Matched baseline comparison for 1 actionable alert, 1 independent cluster

| Horizon | V3.1 net | Random direction net | Prior closed-15m momentum net | Model - baseline |
| --- | ---: | ---: | ---: | ---: |
| 15m | -0.25678% | -0.34320% | -0.34320% | +0.08642 percentage points |
| 60m | -0.55853% | -0.04080% | -0.04080% | -0.51773 percentage points |
| 180m | not matured | not matured | not matured | N/A |

The random and momentum controls happened to choose the same direction in this only event; they do not constitute independent confirming evidence. **n = 1, unique market clusters = 1; no defensible 95% confidence interval.**

## Actual observed source-health

- Sole actionable TRIGGERED: GATE_FUTURES, data cohort SPOT_PLUS_GATE, quality FULL, 1/1 reported as complete from one venue.
- Neither that one signal's outcome nor any of the above use actual Gate Futures executable fills; outcomes are **Binance Spot 1m proxy**.
- Old event lacks frozen config hash / commit SHA, faithfully reported as HISTORICAL_MISSING. New passive event provenance is only on the draft branch, not live.

## Next evaluation without changing production

1. Audit 289 RESET_DIRECTION_OR_LEVEL events, distinguish expected refresh versus unstable/watchlist-churn effects using time-to-reset and later prices.
2. Calculate event-based loss and missed-move rates for DROPPED_FROM_WATCHLIST (154) and WATCHLIST_GRACE_EXPIRED (73) without selection bias.
3. Continue to collect **confirmed TRIGGERED** events, ideally hundreds of *independent clusters*. Report side, market regime, venue, T1/stop/timeouts, net R and delays separately.
4. Fix native perpetual venue data access and same-venue executable outcome validation in a new explicit version; avoid treating a Spot proxy as actual profit.
5. Do not relax frozen V3.1 signal safety conditions based on four stage messages or one actionable alert.

**Implementation status:** read-only validation branch CI passed; first real-record audit ran successfully. All production rules on main remain unchanged; all new scripts and this report remain in [draft PR #28](https://github.com/hizbyavuz/-avci2-v2/pull/28).
