# V2.2 structural stop correction — 23 October 2026

The MERL screenshot reports a SHORT reference of 0.0285, a break boundary of
0.028569231, a stop of 0.028556714, and a target of 0.02798. The old execution
check admits that geometry for plausible ATR values even though a return to the
reported boundary crosses the stop first. The screenshot's arithmetic is
reproduced in a regression test. This is **not** a replay of the original MERL
OHLC, pivot selection, entry, or outcome: those live candidate rows were not
available in this checkout. No claim is made that its wedge was correctly fitted
or that a different stop would have made the trade profitable.

## New contract

- BREAK SHORT: use the greatest of the frozen break boundary, last confirmed
  opposite pivot, and closed highs from that pivot through the confirmation
  candle; add the existing 0.20 ATR buffer. LONG mirrors this with lows/minimum.
- RETEST: use the frozen boundary and adverse extremes of the retest and its
  confirmation candle, then apply the same buffer. It remains a retest-specific
  stop, not the full original pattern's stop.
- Recalculate reward/risk from that stop and the existing first obstacle. The
  1.5 minimum net R, 0.30% cost assumption, targets, chase limit, detection rules,
  setup timeframes, and notification limits are unchanged. A wider structural
  stop can reject a previously eligible plan. Targets are not stretched to
  recover R. No new trend, EMA, volume, or retest requirement is introduced.
- New published plans must pass a stop-evidence consistency check again at
  publication and simulated entry. Missing or inconsistent evidence fails closed.
- Version is `PATTERN_STRUCTURE_RESEARCH_V2_2_2026_10_23`; CONFIG includes the new
  stop-policy identifier. V2/V2.1 plans keep their original stop and entry
  contract. No existing plan is rewritten. Report aggregates are split by version.

## Evidence

Candidate payloads and `PATTERN_CANDIDATE_AUDIT` record pivot prices and known
times, fitted line coefficients, their index origin, selected lookback window,
confirmation time, stop reference/extreme/window/buffer, target, R, and rejection
reasons. These are assessments, not deliveries. `PATTERN_PLAN_DELIVERY` records
the actual receipt state; `PATTERN_PLAN_OUTCOME` logs the first measured exit.
The Telegram card retains the existing concept and explains the new stop basis.

## Validation

63 tests pass across `test_pattern_stop_invalidation`, `test_pattern_engine`,
`test_pattern_delivery_operations`, `test_futures_history_worker`,
`test_futures_window_prewarm`, and `test_history_symbol_isolation`.
The five initial regression cases failed before the correction and pass after it.
Additional checks cover mirrored long/short retest wicks, rejection after a wider
stop reduces R, no future/open candle use, old stop/outcome preservation,
version-separated reporting, and reconstructible assessment logs. Network sends
are mocked; no Telegram test or exchange order is sent by these tests.

## Remaining limitations

This fixes stop consistency, not the statistical validity of wedge detection.
It includes the previously prepared V2.1 message/history-priority change because
it is based on e61f23d. The single live candle connection's 166-coin capacity is
unchanged. Complete live coverage and the original MERL pivot record still need
separate verification. Funding is still excluded from the fee/slippage-only R.
