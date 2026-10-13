# Final logic, math and notification audit

The observer is running in production with verified feed coverage for 181
eligible instruments. At 18:38 UTC, 178 had complete ready short histories,
three remained explicit data failures, five transitions had Telegram ACKs,
and there had been no feed disconnects. These are operational observations,
not evidence of positive trading expectancy.

## Corrections

* The legacy live pool's SHORT gain-before-confirmation metric used
  `(early / current - 1) * 100`, which reports 11.11% for 100 -> 90.
  Future records now use the original-price denominator and direction sign,
  reporting 10%. Core trade outcome labels and the new observer already use
  that denominator; their scoring and execution rules are untouched. Older
  persisted metric values are not silently rewritten.
* Confirmation/trigger message volume now receives the actual evaluated
  closed-candle quality snapshot. It no longer falls back to an older plan's
  volume while describing a newly confirmed candle.
* A passed combined quality check does not imply every individual feature
  passed. User text says the breakout check passed, without falsely claiming
  that volume independently passed.
* Invalidation messages include symbol and direction in their header so the
  existing unified card is updated instead of sending a separate old-format
  message. Unicode catalogue symbols can also route into the same card.
* Unreachable duplicate TRIGGERED message formatting was removed.

## User display

All recorded market fields occupy one logical evidence line, with explicit
1m/5m volume windows, understandable names, record time and missing-data labels.
Core and early references remain distinct; two different locked levels are
never silently merged into one. One direction/status header and one stop/target
line replace multiple repeated technical summaries. Missing inputs are not
shown as zero, and stale analyses are not displayed as current evidence.

## Validation limits

The existing selection, frozen thresholds, four-factor gates, trade
authorization, episode references, costs and no-order policy are unchanged.
Ready coverage is not 181/181 while the three data failures remain. The native
history relay rejects three Unicode symbols; these remain unresolved rather
than ordinary false-positive failures. This audit does not claim every model
choice is optimal, every move is predictable or the strategy has a proven edge.

Validation covers signed percentage returns, closed/future/gapped data, locked
episodes, pre-signal exclusion, current-candle display, one evidence line,
Unicode routing, missing/stale values and persistent Telegram card behavior.
