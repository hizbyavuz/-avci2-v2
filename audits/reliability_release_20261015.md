# Reliability correction and independent evidence — 15 October 2026

## Scope and authority

User delegated the repair and verification of the existing LONG/SHORT system.
Baseline: `d4eaa08f4117d31a9ab05539f3485db330fbccf3`, Railway production
deployment `8ba712dc-c914-4c44-8787-4251aa4c9887`. This is a corrective V3.2
cohort, not a claim that the strategy has a positive expected return.

Live evidence at 18:20–18:25 UTC: 168 assessed symbols per scan; around 98
liquid symbols requested and covered by native candle feeds. No kline gaps or
disconnects in the sampled health logs. BLUR's WATCH was acknowledged at
17:56:19 UTC and its locked stop was touched by 18:12 UTC. A later READY
assessment at 18:24 UTC had zero creations/deliveries; the existing one-hour
same-symbol creation cooldown explains this case. Cooldown was not relaxed.

## Reproduced defects and corrections

Four regression tests fail against the exact baseline and pass after repair:

1. A closed break older than the dispatcher's existing 75-second limit still
   appeared READY in the assessment. Assessment now rejects the stale
   confirmation explicitly; no new timing threshold was introduced.
2. While advancing an existing idea, a changed direction expired the idea,
   but a subsequent `REVERSAL_` rejection could overwrite EXPIRED with WATCH.
   The reversal hold now applies only to still-live WATCH/READY stages.
   Time and price expiry remain terminal too (those paths already worked).
3. Outcome computation used the current process cost instead of the cost
   frozen in the original observation. The record's declared cost is now
   used; legacy V2/V3 use their saved geometry cost or their fixed .30%.
4. Assessment deduplication used the current global cohort and omitted
   delivered episode identity, collapsing distinct hypothetical deliveries.
   Keys now include the recorded cohort and delivery episode identity.

## Final-gate evidence and validation

`dispatch_audit` retains bounded, five-minute-deduplicated evidence for
active-episode suppression, cooldown, notice cap, fresh-price loss, stale
confirmation, geometry expiry, direction changes, path gaps, and delivery
failures. Scan summaries include final creation gates. No secret credentials
or Telegram recipient identifiers are logged.

An independent read-only report runs every 15 minutes from the existing health
thread. It separates strategy identity, delivery stage and 15/60/180-minute
horizons; 180 minutes remains the declared primary endpoint. It reports saved
reference-model net outcomes, raw directional endpoint behavior, unresolved
data, known partial-minute pessimistic sensitivity and final-gate counts.
WATCH/rejected observations are never counted as entries. Equal-random side
comparison is a raw endpoint diagnostic on the same observation, not an
executable strategy benchmark. Delivery and observation query budgets are
separate; truncation and missing outcomes are explicit. Old cohorts remain
visible after a release instead of appearing to disappear.

Output: existing persistent state directory, `chart_reliability_latest.json`,
plus bounded `CHART_RELIABILITY_HEALTH` / `CHART_RELIABILITY_COHORT` log lines.
This adds no process, external service, order API, subscription or live trade.

## Validation and limits

- Baseline suite: 639 tests passed.
- Corrected full suite: 649 tests passed; targeted chart suite also passed
  after strengthening the changed-direction regression.
- Four targeted baseline regressions: four failures, as expected.
- `git diff --check` passed.
- Existing setup thresholds, .30% reference cost, structural stop/obstacle,
  cooldown and payoff policy are unchanged. New source hash separates V3.2.
- Actual entry fills, exchange fees/slippage/funding and out-of-sample
  executable benchmark superiority remain unproven. The report deliberately
  says `UNVALIDATED_REFERENCE_MODEL`, never a fabricated success probability.
- Small, correlated intraday samples cannot establish a 60–70% win rate.

Deployment must be verified as SUCCESS and runtime health observed before
calling the corrective release live. Rollback pins the baseline commit above;
all old assessments and outcome records remain intact.
