# Movement context V1 — observational, 2026-10-14

Problem: the early observer's 30-minute box and 10-minute volume baseline
identify a local breakout, not the start of a broader market move. A late
continuation and a fresh move can therefore receive the same early label.

This version adds measurements, not a signal score or a new veto. It does not
change entry thresholds, direction, message wording, or execution rules.

Fixed measurement windows: 96 closed 15m bars (24h), 48 closed 1h bars (48h),
42 closed 4h bars (7d). Each records range position, pre-event return, EMA20
slope and directional distance in ATR units, recent four-bar quote volume
relative to the prior four bars, directional change in taker-buy share, and
distance to the nearest confirmed historical pivot in the trade direction.
These are diagnostic windows, not claims of optimal trading horizons.

At EARLY and CONFIRMED transitions the observer stores the measurements in
transition features and emits MOVE_CONTEXT_OBSERVATION. The decision is made
before this sidecar runs. Existing hourly MESSAGE_DIRECTION_RESULT reports
attach reconstructed context to retained directional messages, alongside
15/30/60/180-minute gross direction outcomes. Terminal messages are excluded.

All candles must close strictly before the event, have native verified sources,
valid OHLC/flow, contiguous timestamps, and a fresh endpoint. Incomplete
timeframes remain explicit. No identified pivot means UNKNOWN, not clear room.
Historical reconstruction may use candles backfilled later; it is not proof
that those data were available in real time. Newly captured observations are
separately tagged observed_at_event.

Evaluation next: join observations by episode, separate first ideas from card
updates and later confirmations, compare continuation with reversal by horizon
and BTC regime. Do not count pre-event moves as performance. Gross endpoint
direction is not executable profit, MFE, MAE, or target/stop ordering. Derive
any prospective phase rules on a separate development sample and validate out
of sample before proposing production filters. Do not optimize frozen rules.

Validation: new tests cover future-candle exclusion, LONG/SHORT sign symmetry,
unknown obstacles, missing history, source and flow validation, and missing
references. Full repository suite: 440 tests passed.
