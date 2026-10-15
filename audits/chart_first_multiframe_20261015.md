# Chart-first manual decision support, 2026-10-15

User requested graph-led LONG/SHORT suggestions with derivatives as supporting
context, explicit entry/invalidation levels, and manual execution/exit decisions.
User additionally required higher timeframes, not only 5m/15m.

## Version boundary

`CHART_FIRST_MULTIFRAME_V1_2026_10_15` is a new hypothesis. Existing weighted
42-point / 12-edge selection, frozen datasets and paper rules are preserved.
The legacy analyst/live pool continue recording comparisons, but their outgoing
trade notifications are suppressed explicitly, without fabricated delivery ACKs.
New mode is enabled by the native daemon default `LS_CHART_FIRST_V1=1`.
Rollback: set `LS_CHART_FIRST_V1=0` and restart/redeploy. Source and all old
outcomes remain available; do not mix the new cohort with historical performance.

## Actual decision order

1. Verified Binance Futures market catalogue, native ticker, fresh quote, and
   complete closed 5m/15m/1h/4h candle histories. Same original 25M volume floor.
2. Daily context is exactly aggregated from complete UTC days of six native 4h
   candles. Require at least 20 complete days. The old strict native reader
   cannot fetch `1d`; this new path fixes daily coverage without another API.
3. Confirmed swings on 1h determine the candidate direction. 4h distinguishes
   continuation from countertrend reversal attempts, which need a 1h structure
   break. Daily direction and levels provide context, not a consensus veto.
   A 5m bounce cannot independently reverse the hourly direction.
4. 15m checks whether the counter-direction pullback is still in progress; 5m
   confirmed swing levels define entry and invalidation. A closed break can be
   READY; an approaching, turning setup can be WATCH. Old breaks/extended entry
   and a first obstacle within the cost allowance are rejected, silently.
5. Never reclassify an already-cleared historical resistance as an untouched
   next obstacle. Never skip the *first unbroken* obstacle to manufacture room.
6. Missing OI, funding, taker flow and account ratio remain null and are disclosed.
   They cannot select, flip or veto direction. Available validated cached fields
   are shown/interpreted; candidates enter the existing collector's priority queue.

No 42-point requirement, 12-point edge, fixed 0.9% room or mandatory 1R gate is
used in the new mode. Reward/risk to the first chart obstacle remains visible as
a warning when poor. This is NOT proof that removing those gates improves returns.
The 0.30% round-trip cost allowance is a declared conservative model assumption,
not a measured fill or fee promise. Entry range is capped so the advertised
range does not extend inside that cost allowance. No fixed dollar-profit goals,
position-sizing prescriptions, actual exchange orders or automated exits exist.

## Delivery, records and scope

- Messages: green LONG/red SHORT; daily/4h/1h context, why, entry condition or
  current entry region, invalidation, first obstacle, supporting evidence.
- WATCH and READY are distinct; a new READY notification is not silently edited
  into an old watch. Completions edit the last card. Maximum 3 new ideas per
  five-minute window and a 1h per-symbol cooldown. No quota fills.
- Native quote must be <=5 seconds old. A fresh READY requires a <=75-second-old
  closed 5m candle. Uncertain initial Telegram sends are never blindly retried.
- Once an episode exists its trigger, stop and obstacle remain locked. A crossed
  obstacle ends the idea; the target is never moved farther to hide a late entry.
- Rejections and candidates are retained, deduplicated by 15m/structural idea/state.
  Accepted messages get separate DELIVERED snapshots using post-ACK native quotes.
- New compact SQLite tables keep configuration, source-derived strategy ID,
  assessments, episodes, transitions and 15m/1h/3h subsequent price paths. Missing
  paths are UNRESOLVED, never silently scored as losses. Same-minute level
  ambiguity is invalidation-first. Paths start at the next full minute, clearly
  labelled as gross observations, NOT executed or net trading PnL.
- Runtime logs report every rejection reason, eligible side/scenario and delivery
  counts. Technical tests verify symmetry, data integrity, missing-field invariance,
  locked geometry, no false delivery ACK and first-barrier ordering.

Neither unit tests nor deployment health validate profitability or a 60–70% win
rate. Forward outcomes and comparison against the legacy cohort are still needed.
