# STRUCTURE_CONTEXT_V1 — fixed offline review protocol

This is a new hypothesis version. No frozen predecessor is modified, no Railway
service is started, and no Telegram call is made by this replay.

## Predeclared scope

- Symbols: BTCUSDT, ETHUSDT, SOLUSDT, ADAUSDT, DUSKUSDT, KERNELUSDT.
- Development inspection: 2026-08-15 00:00 through 2026-08-22 00:00 UTC.
- Separate validation window: 2026-09-15 00:00 through 2026-09-22 00:00 UTC.
- Primary setup: 15m; independently reported alternative: 30m.
- Context: closed 1h and 4h candles. No timeframe unanimity gate.
- Full monthly 1m archives provide warmup, aggregation and 3h outcomes.
- Verify each official .CHECKSUM. A missing archive is a data failure, not a loss.
- Freeze config and code hashes before replay. Do not change decision thresholds
  after observing either window. If a genuine bug is found, log it, rerun the
  whole scope, and do not call the already viewed period untouched holdout data.

The six-symbol sample is deliberately small, selected before inspecting these
outcomes. It cannot establish full Binance-universe recall, listing eligibility,
tradability, order-book fills, live delivery reliability or profitability.

## Decisions and drawings

Use 2-left/2-right closed-candle pivots, alternating legs of at least three bars
and 1.5 times the larger endpoint ATR. The ATR uses only past candles at that
endpoint. Seed pivots are not significant until an opposite leg qualifies.
An outside candle with ambiguous high/low order is not two ordered pivots.

The last significant high/low supplies a decision zone of ±0.15 endpoint ATR.
An approach within one setup ATR is WATCH. A first close beyond the zone plus
0.05 ATR is CONDITION_MET. A return into the frozen zone followed by a directional
close through its trigger within six bars is RETEST_HELD. A break extended more
than 0.75 ATR is LATE. Double top/bottom is a descriptive tag only when the two
extremes straddle the same decision pivot and differ by no more than 0.35 ATR.

Stop: actual adverse extreme since the opposite confirmed pivot, plus 0.2 ATR.
For a retest, keep the origin stop; do not widen it to make the plan look better.
Target: nearest still-unbroken significant opposing zone across setup/1h/4h;
use its near edge, not a synthetic distant target. Unknown target stays unknown.
Qualifying plan geometry additionally requires 1.5 net R under a 0.30% round-trip
fee/slippage assumption. This test never suppresses the initial WATCH record.

Fibonacci is an annotation of the significant directional leg ending at the
decision pivot. Store A/B timestamps, confirmation times and prices. It does not
independently choose direction. No FVG/IFVG/order-block claims are fabricated.

All charts use immutable decision coordinates and exclude future candles.
No drawing is recomputed retrospectively from the outcome.

## Measurement

- Deduplicate events and first WATCH per idea. Keep all late/failed/low-room cases.
- First full minute after decision: reference entry; no pre-signal gain counted.
- Observe +1/+3/+5/+7/+10/+15 reach and time, raw MFE/MAE for three hours.
- Raw excursion and fixed-horizon return are NOT stopped-plan profits.
- Only eligible CONDITION_MET plans get hypothetical stop/target/timeout exits.
- If stop and target hit in one minute, stop first. Gap stop uses minute open.
- Entry with no valid remaining geometry is cancelled, not silently a loss.
- Missing entry/path/horizon stays explicitly unresolved. Funding/full net unknown.
- Baselines: seeded random time and direction on the same symbol/day; simple
  first close through the preceding 20-bar high/low. Compare fixed-horizon
  returns with the same delay/cost, not mismatched stop/target profits.
- Report primary and alternative separately, stage/direction counts, daily counts,
  complete vs unresolved denominators, fee-only plan results, and baseline means.
- These observations are correlated; do not call events independent trades or
  claim a calibrated probability from a small sample.

## Notification simulation and resource ownership

Record every event regardless of notification policy. WATCH gets a 60-minute
symbol/interval cooldown. State promotion is separate. At most three notices per
scan cycle and 20 per rolling day; expose every suppression reason. These are
delivery limits, not success criteria. Never count WATCH as an executable trade.

The new runtime reads the existing collector DB in read-only mode and starts no
collector itself. It processes each closed setup candle once, keeps compact scan
records, and stores full compressed snapshots only for new events. It has
conservative process/disk guards (768 MiB peak RSS, 4 GiB state directory, 512 MiB
free-disk floor). These do NOT impose Railway billing caps or limit old workers.
Reaching a guard stops new work and preserves evidence; no automatic deletion.
