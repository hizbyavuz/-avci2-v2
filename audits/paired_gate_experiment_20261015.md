# Paired gate experiment V1

Production four-factor admission, V5 geometry and live confirmations stay as the
reference. This release fixes a data-integrity bug: with verified market identity
and a fresh snapshot, one invalid derivative component no longer erases valid
independently checked components. Missing values remain None, never fabricated.
Strict validation remains the default API; snapshot readers can retain partial
evidence. Existing 3/4 research can consequently measure missing OI, funding or
account ratio as well as missing taker. Unverified/stale whole snapshots still fail.

At each completed full/partial analysis, freeze six arms using the same scores,
closed candles, entry, target, cost and thresholds: baseline; one missing derivative
only; observed last-eight-5m-bar stop only; room floor removed only; net-R floor
removed only; local stop plus one missing derivative. The combined arm is explicitly
separate. The stop alternative is a hypothesis, not an established improvement.
No arm bypasses unknown direction, venue, liquidity, volatility, invalid levels or
an independent structural obstacle. Removed gates do not authorize production.

Freeze all arms together for the first episode per symbol/direction/strategy in
361 minutes. For admitted arms compare causal next-minute, closed-5m-breakout, and
subsequent retest-plus-close probes. The latter two are simplified research probes,
not replicas of production body/volume/order-book confirmation. No entry inside an
unfinished candle; pre-entry invalidation cancels the probe. Up to 180 minutes to
observe confirmation and 180 minutes holding after entry. Same fixed stop/target,
existing fee/slippage assumption and realized funding calculation. Missing paths
stay unresolved, unconfirmed entries remain NO_CONFIRMATION, inadmissible arms
are not counted as losing trades. Counts alone cannot establish profitability.

Existing raw 15/30/60/180-minute labels remain separate. The experiment records
are in the existing durable data_opportunity_v2.db. Reports use PAIRED_GATE_RESULT;
candidate-level decisions use PAIRED_GATE_CANDIDATE. Old observations without these
frozen inputs are not retrospectively relabeled as prospective experiment samples.
No Telegram sends, no orders, and no automatic promotion of an experimental arm.
