# Same-risk peer and exit comparison — 23 October 2026 (Europe/Istanbul)

This comparison is specified after the existing V1 and SHORT V2 results were
seen, but before computing peer returns or fixed-2R returns. Both August and
September are previously viewed development data. This is NOT independent
validation. No live, Telegram, exchange order, network download or parameter
search is part of this experiment.

## Question and frozen inputs

Does the signal select better direction opportunities than a contemporaneous
volume/volatility-matched peer, and does the common trailing exit explain the
negative SHORT result? Use every existing 625 August V1 candidate plus all
1,523 SHORT V2 August/September candidates. Keep invalid and unavailable entries
in the denominators. No new signal model, stop widening, 1R target or hit-rate
optimization. Existing selection, runtime and cost modules remain unchanged.

Sources: preserved hourly ZIP manifests for August (833 local hashes, no official
CHECKSUM proof) and September (869 official CHECKSUMs), and 479 previously
verified minute ZIPs. Verify local hashes; never download a replacement. The
historical universe is unclassified and includes noncrypto contracts.

## Select the peer before looking for minute/outcome availability

At each signal confirmation hour, build the full available hourly panel. Require
169 consecutive closed valid OHLC hours, valid quote/taker volumes, positive
ATR14 and at least 2M USDT quote turnover in the last 24 hours. No future data or
future availability is a selection input. The signal coin cannot be its peer.
Other signal coins ARE eligible: this is a simple matched random-entry baseline,
not a claimed no-signal or near-miss population.

Distance = abs(log(peer 24h quote / signal 24h quote)) +
abs(log(peer ATR14/close / signal ATR14/close)). Sort by distance then symbol;
take up to the five nearest. Choose one with SHA256 of
`exit-control-v1:20261023:{model}:{symbol}:{confirmation_ms}` modulo that count.
Write MATCHES.json.gz before computing new minute outcomes. Do not replace a
selected peer when its minute entry or forward path is missing.

## Shared execution assumptions

Signal entry remains its original first FULL minute after observation. Reuse
the original geometry gate and structure stop; reproduce each prior measured
signal outcome before interpreting new comparisons. Invalid signal entry means
no executable signal/peer pair; the peer choice is still retained.

The control enters its same-minute open on the SAME side. Its stop distance as a
percentage of entry equals the signal's actual initial risk percentage, known
at that simultaneous entry. Its stop is a risk-matched synthetic benchmark stop,
not a claimed chart support/resistance. Its own pre-decision ATR is used for the
shared trailing buffer. No leverage or portfolio sizing assumption.

Two exits only, for signal and peer alike:

1. FROZEN_TRAIL: unchanged V1 +2R activation, completed UTC 4h structure trail,
   168h maximum, same-minute stop first, worse opening price through stop.
2. FIXED_2R: unchanged initial stop, resting take-profit at 2 initial R, 168h
   maximum. Stop first if both barriers occur in a minute; a gap through stop
   fills at the worse open. A favorable gap fills at the target, without claiming
   a better fill. This is a predeclared diagnostic exit, not a new approved rule.

Both subtract the same 0.30% fee/slippage scenario. Funding is UNKNOWN, never
zero or labelled fully net. Missing/gap outcomes remain explicit. Known exits
before a later gap are recorded separately from full 168h path coverage.

## Report without selecting a winner

For each model/month/side report all candidates, source/entry statuses, exact
pair availability, selected peer count and unique coin/hour/day counts. Primary
paired tables use only pairs with complete signal AND peer 168h paths; record
all excluded/missing pairs and the convenience-sample limitation. Show signal
and peer mean fee-only R, positive counts, mean signal-minus-peer R, and mean
fixed-minus-trailing R. Also show both exits on the SAME full-path signal cohort,
so peer availability cannot manufacture an exit improvement.

Use descriptive 90% intervals for paired differences with non-circular 7-day
moving calendar blocks, 2,000 repeats, seed 20261023. Outcomes overlap for up to
seven days; few blocks and repeated coins prevent independent-validation claims.
Do not interpret a positive subset or interval as live approval. Retain every
variant, pair and unsuccessful result; no later threshold revision in this run.

The experiment cannot enable rejected SHORT models or validate LONG by itself.
Deployment and notifications stay unchanged. Report a failed or inconclusive
result as such, even if it means the requested two-sided motor is unfinished.
