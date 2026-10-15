# Chart-led manual suggestions: V2 intervention, 2026-10-15

User approved implementing two explicit setups and repairing the three findings
from the 15:52–16:33 Turkey-time audit. Previous source is preserved at GitHub
commit `37037da134751d0abdbdb2de317a25e7c61878c8`. This is a new hypothesis and
cohort, `CHART_FIRST_TWO_SETUPS_V2_2026_10_15`, not a retrospective improvement
to V1 results. No orders, account access or dollar-profit targets are added.

## Fixed rules declared before deployment

- Native Binance Futures price and closed-candle health requirements remain.
  Daily and 4h provide context, 1h selects direction, 15m describes the current
  move, 5m supplies the setup. Daily/15m disagreement is disclosed. 4h opposition
  is recorded as `COUNTERTREND_OBSERVATION_ONLY`, with no entry notification.
- Compression: six closed candles, average range at most 0.80 of the preceding
  20 true ranges; box width at most 2.5 of that baseline range. WATCH requires
  an approaching directional candle; READY requires a fresh closed box break.
- Pullback continuation: a confirmed directional swing within 24 bars, at least
  two counter-direction closes into a trough that retains a higher low (mirrored
  lower high for SHORT), pullback depth at least 0.5 baseline range, directional
  recovery within 1–4 bars of the trough. Trigger is the trough candle's opposite
  extreme. Invalidation is the actual trough/box boundary with 0.10 baseline
  range buffer. The stop is never fitted to make a payoff ratio pass.
- Both models use identical reflected LONG/SHORT logic. The compression model
  takes precedence if both qualify; this precedence is part of the fixed model.
- The first distinct confirmed pivot region across 5m/15m/1h/4h/daily remains
  an obstacle even if an earlier close crossed it. A failed breakout no longer
  deletes old resistance/support. A crossed first obstacle is never replaced
  with a farther target. Missing obstacle means no advertised entry plan.
- Entry quality: net room to the first obstacle must be at least the loss to
  structural invalidation including the same 0.30% round-trip cost assumption.
  This 1:1 **net** payoff floor is an explicit conservative selection policy,
  not a measured success probability, fee quote or claim of positive expectancy.
  It is neither the old gross 1R gate nor the old fixed 0.90% move floor.
  Expected return still requires observed win/loss frequencies.
- If direction sign is `s`, entry `e`, obstacle `t`, stop `b`, cost fraction `c`,
  net reward is `s*(t-e)-c*e`; net loss is `s*(e-b)+c*e`. For required ratio `r`,
  the quality boundary is `(t+r*b)/((1+r)*(1+s*c))`. The advertised entry interval
  is bounded by this quality boundary and the existing one-ATR chase limit.
- Revalidate the frozen plan with the live quote immediately before dispatch.
  A fresh entry requires a <=75-second-old closed 5m break, with the previous
  close still on the pre-break side. A nearer newly confirmed obstacle retires
  the old watch idea rather than moving its target. Delivered entries retain
  their original stop/obstacle for outcome evaluation.
- Auxiliary OI, funding, account ratios and aggressive flow remain supporting
  information. Missing/stale auxiliary fields cannot change direction or veto
  a setup. Active watches also refresh cached support before READY.

## Records and interpretation

Existing `chart_first_v1.db` is an append-only cohort store despite its original
filename. Assessment payloads and manifests carry version/config/source hashes;
new identity hashes include engine, worker, setup and outcome modules. Old rows
and their original next-full-minute labeling semantics are retained. Old active
cards expire as prior-version evaluations; this does not close user positions.

The V2 label includes the notification's partial first minute. If a level lies
inside that minute's high/low, minute OHLC alone cannot establish whether the
hit happened before or after notification: label `UNRESOLVED_BOUNDARY`, exclude
from primary net-return statistics, and retain a separately named pessimistic
stop sensitivity. Do not claim tick-exact coverage. MFE/MAE are explicit bounds
when pre-notification extrema are unknown. Missing first-minute bars stay
unresolved; they are no longer silently discarded.

Endpoints are the first minute close at or after each 15/60/180-minute horizon;
the actual endpoint and effective duration are recorded. Only DELIVERED_READY
observations receive locked-level/horizon reference-model returns with assumed
costs. WATCH/rejected observations are never counted as hypothetical filled
entries. Same complete minute hits use stop-first; a gap through a stop uses
the observed open. These are reference models, not actual user fills or PnL.

Due-horizon selection prevents already measured 15m rows from starving newer
rows while waiting for 60m/180m. Five-minute reports separate setup, direction,
stage and horizon, with unresolved counts, average model net return and profit
factor. Horizons must not be pooled as independent trades. Detailed delivery
logs retain the actual accepted-message reference, locked levels and geometry.

## Verification and operational boundaries

Regression cases cover both setups, mirrored LONG/SHORT behavior, the observed
JTO poor-payoff example, the full advertised interval, failed breakout levels,
late/old breaks, preserved levels, missing support invariance, first-minute
ambiguity, incomplete data, stop-first, gap stops and non-entry observations.

Unit tests and successful deployment prove functionality, not profitable edge.
No thresholds will be changed in response to a handful of live winners/losers.
Further rule changes require a new version and fresh outcome cohort. Rollback
is repinning the preserved prior commit; raw history and research records stay.
