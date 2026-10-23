# Pattern V2.3: complete candle coverage and observable scan decisions

The scanner used the full eligible universe, while pattern candle demand ignored
`full_chart` and truncated to 166 symbols. Both the live stream and history
repair consumed that truncated list. At 09:36 UTC on 2026-10-23 the live service
reported 285 eligible symbols, 166 live subscriptions, and 238 stale
symbol/timeframe pairs. Missing data is not evidence of an absent opportunity.

## Change

- Pattern candle/history demand includes the entire eligible universe, BTC, and
  saved active plans. Auxiliary derivative collection retains its existing bound.
- A pool allocates symbols to independent WebSockets of at most 166 symbols / 996
  streams. Assignments survive hourly rank changes; reconnects remain independent.
  Each stream has its own SQLite connection and the existing buffered retry path.
- Pool health distinguishes assigned subscriptions, connected subscriptions and
  recently received prices. Closed-history readiness is reported separately.
- Repair checks the same latest 220-bar window, minimum length and freshness as
  the pattern reader for 15m/30m/4h. A gap outside the older repair check's 120-bar
  tail can no longer leave a pattern scan permanently blocked. A valid 40-bar 4h
  history is no longer repeatedly repaired just because it has fewer than 220 bars.
- Each successful scan persists its shape/break/retest/rejection counters. Health
  combines new and already-completed scans for the current closed 15m/30m bars,
  so `newly_scanned=0` no longer looks like zero coverage. Old uninstrumented scans
  are explicitly unclassified. Publication counters count attempts, not unique
  signals; unique candidate additions are reported separately.
- New version: `PATTERN_STRUCTURE_RESEARCH_V2_3_2026_10_23`. Collector code is now
  included in its manifest. Stored V2/V2.1/V2.2 plans retain their original contract.

No entry, shape, stop, target, cost, R threshold, universe eligibility, notice
limit, Telegram destination, or execution rule is changed. No order API is added.
Larger coverage does not establish signal quality or guarantee a notice count.

## Verification

94 focused tests pass, including 749 eligible symbols plus BTC and an active plan,
stable five-shard allocation, simultaneous real stream loops over fake transports
persisting to one SQLite database, clean shutdown, stale/unverified price handling,
historical repair/reader agreement, and scan counters without duplicate counting.
The prior stop, closed-candle, delivery, outcome, history and isolation tests pass.

Live verification must separately observe deployment SUCCESS, the intended commit,
full current subscription/price coverage, history repair progress, and current-bar
scan readiness. A successful build alone is not sufficient.

## V2.3.1 live verification follow-up

V2.3 reached SUCCESS at 09:52 UTC; two streams received current prices for all 287
requested symbols. Verification then found the 24h-volume catalogue temporarily
shrinking from 287 to 181 symbols when ticker freshness varied. Immediate removal
in the pool unnecessarily unsubscribed those symbols before they returned.

V2.3.1 retains their shard assignments for the collector's existing 24h retention
period. This is collection only: scan eligibility and freshness checks are unchanged.
An additional regression checks dropout, return without movement, and expiry.
