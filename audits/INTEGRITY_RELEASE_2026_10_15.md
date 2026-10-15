# LONG/SHORT integrity release — 2026-10-15

Release identity: `LS_AUDIT_INTEGRITY_V1_2026_10_15`.
Scope: existing Binance Futures signal/paper engine. No automatic orders.

The historical `LONG_SHORT_V3_FROZEN_CONFIG.json` describes the earlier shadow
hard-gate design. It is preserved as historical evidence, not the effective
production contract for this release. `strategy_manifests` in the live database
records effective numeric settings, mode flags and source digest. Analyst plans
and events carry references; paper cohorts include both execution-code identity
and locked-plan identity. Historical results are not relabelled as this release.

## Effective decision contract

- Production direction uses the existing additive score: minimum 42, minimum
  direction difference 12. Trend/structure/momentum/derivatives/BTC components
  remain; weights were not optimized. These points are not win probabilities.
- OI, funding, taker and account-ratio completeness are mandatory. Their values
  contribute to direction; completeness does not mean directional agreement.
- Order-book imbalance contributes up to 5 direction points and a Futures book
  is also required for entry-cost validation. The 4h trend contributes to direction.
- Historical V3 hard gates are shadow diagnostics; spot flow is disabled in
  Futures-only mode. The additive model does not implement every shadow veto.
- Structural-plan V3 retains the stop and targets the first distinct confirmed
  price band. Existing minimum room/net-R/cost thresholds remain unchanged.
- FAST entry requires qualified acceptance closes AND the current price still
  beyond the trigger. RETEST entry requires a qualified candle closed strictly
  after the recorded retest touch, with current price beyond the trigger/zone.
- A terminal same-direction setup can rearm on a later analyst scan with a new
  trigger level, if its paper trade is no longer open. The new episode requires
  a new close; an identical scan cannot replay the old signal. Active episode
  levels remain locked. New strategy versions create separate episodes.
- RSI with zero gains and zero losses is neutral. BTC residual pairs identical,
  contiguous hourly timestamps; bad/missing alignment produces no residual bonus.

## Recording and notification

Watch-state transition, event and pending core-message record commit atomically
before any send. A storage exception rolls the transition back. An optimistic
stage/episode check prevents stale concurrent snapshots overwriting newer state.
Recovered, unclaimed messages require time and current market revalidation.
The existing 75-second freshness limit is checked again after revalidation.

Telegram has no sendMessage idempotency key. Core sends use one network attempt.
A timeout or interrupted claimed send is UNKNOWN, never assumed delivered and
never blindly replayed. This avoids duplicates but cannot guarantee delivery in
an ambiguous transport failure. The independently recorded trade remains measurable.
Old observational notifications are outside this core-outbox migration.

## Measurement and explicit remaining limits

Independent paper entry remains the next full one-minute open. TP1/SL are fixed,
cost assumptions and actual historical funding are included, missing data stays
unresolved, ambiguous intrabar order is stop-first, and the horizon remains 180
minutes. Grouping now also separates effective strategy/plan identity.

This release does not add partial TP, a trailing stop, calibrated win
probabilities, a fitted holding period, account-level position sizing, or a new
retention policy. These require distinct validation/design work. Existing
direction-component counterfactuals now use the production direction function,
and explicitly declare that they are not complete entry/exit/PnL ablations.

## Verification and rollout

Run `python -m unittest discover -s tests -p 'test_*.py'` in an isolated checkout.
Regression cases cover both entry directions, post-touch candle chronology,
terminal rearming without duplicate/open-trade replay, zero RSI/close-location,
hourly alignment, identity changes, outbox expiry/uncertain send/recovery, and
the real live loop with mocked market and Telegram I/O. The integration checks
that the event exists before send and that event-storage failure rolls back state.

Deploy the reviewed commit only to the existing main signal service. Confirm
SUCCESS, the running commit, `LS_STRATEGY_EFFECTIVE`, matching analyst strategy
IDs, native data coverage, and independent-paper worker completion. Do not force
a synthetic trade into the user's Telegram to manufacture live validation.

Rollback can restore the prior application commit; schema additions are additive.
Keep new events and cohorts for audit rather than deleting evidence. Profitability
remains unproven until sufficient cost-aware out-of-sample outcomes exist.
