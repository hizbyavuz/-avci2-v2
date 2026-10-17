# Delivery V3 and early-start observation — 17 October 2026

The live V2 worker tied notification admission to at most 12 open hypothetical
plans, although a plan could remain open for 72 hours. ICNT and ZIL examples in
the 17 October audit demonstrated this operational block. This change does not
establish that every blocked candidate would have been profitable.

## Production changes

- Open paper plans no longer consume notification capacity. A persistent rolling
  budget allows at most three initial candidate notifications per 15 minutes.
  Pending and uncertain sends consume this budget too. The existing 24-hour
  per-symbol cooldown, fresh-price checks, stop geometry, chase check and closed
  15-minute entry expiry remain in force.
- A rejected or cooling high-ranked candidate no longer uses one of the three
  send attempts and prevents lower-ranked eligible candidates from being tried.
- First eligibility is stored by symbol and direction for a 24-hour episode.
  Existing V2 paper references can seed this record using their actual observed
  timestamp and quote. Later messages show the first time, price, elapsed minutes
  and directional movement. This is descriptive, not a new optimized age veto;
  later genuinely new 15-minute setups can still qualify under existing rules.
- Production selection thresholds and rank logic are unchanged. The delivery
  intervention receives a new version/config/source hash. The frozen V1.1 module
  is byte-identical and old outcomes are not rewritten.

## Observation only

`EARLY_START_OBSERVER_V1_2026_10_17` reads the already-collected closed 5-minute
futures candles, independently of the production 15-minute scanner. It tests
the same symmetric hypothesis in both directions: a one-hour box after range
compression, a closed breakout, increased 15-minute volume, relative movement
against BTC in the proposed direction, and a still-near current price. It has
no prior 10% leg or 4-hour trend requirement. Constants are hypotheses, not
parameters fitted to the audit winners. Missing/stale/gapped/unverified data
fail closed. No forming candles or external requests are introduced.

These references only enter `research_ideas`, with a distinct observer hash,
three-hour per-direction research cooldown, three-hour measurement duration,
fixed structural stop and no trailing stop. The publish admission function also
rejects `observation_only` candidates. No early-observer Telegram messages or
orders are enabled. Coverage is limited to the current eligible universe and
available local histories; this does not establish full Binance missed-move recall.

## Measurement and validation

New references freeze their measurement contract in their own payload:
15/30 minutes, 1/2/3 hours, and 6/24/72 hours for the production model.
They record 1/2/3/5% milestones alongside the previous larger milestones.
Older references retain their original horizons and outcome version; reports
do not misclassify their absent 2-hour result as a data gap. Stop-first OHLC
handling, first-minute uncertainty, gaps, cost assumption, and post-stop path
separation remain intact. A peak excursion is not a realized return.

Validation: 81 related unit/regression tests passed, including 11 new scenarios
for persistent budget, 12 old open plans, ranked-candidate starvation, restart,
V2 origin seeding, episode/direction separation, expiry, observer isolation,
symmetric early signals, invalid data, new/legacy measurement contracts and
report cohort separation. Existing Telegram tests use mocked HTTP requests.
No test message was sent to the user's Telegram.

Deployment must be verified separately against the exact release commit and
live startup/scan logs. Any follow-up live verification record is evidence of
operation, not evidence of profitability. Rollback uses the previous pinned
commit `72a255ac26bcdd4a082a8f4db6de1556a06e5e7f`; added tables are backward-compatible.
