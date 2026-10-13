# Full-universe early breakout observer V1

The existing 30-coin rotating deep scan can revisit a coin after its initial
move. Its live early layer also requires admission through the core watchlist.
This version independently watches every currently eligible, catalogue-verified
Binance USD-M USDT perpetual, with the same existing universe exclusions and
volume floor. It does not tune rules to OP, VVV, or any named instrument.

## Data and scheduling

Two dedicated, bounded WebSocket connections collect closed 1m/5m candles and
contemporaneous Futures last-trade prices, using the existing idempotent store.
Each connection supports 400 symbols / 800 streams. More than 800 eligible
symbols is an explicit unhealthy state; no silently partial scan is reported
as full coverage. Universe discovery refreshes every 60 seconds, observation
polls every 15 seconds. Missing, gapped, stale, nonfinite, future-dated or wrong
venue data suppresses that coin and is logged; no Spot fallback is permitted.

The existing private read-only Binance history relay bootstraps missing short
histories in the background. Requests are sequential with a one-second gap.
The relay already serializes upstream requests across clients and enforces its
source-wide cooldown. A per-symbol HTTP 400 is isolated; other failures back
off for 60 seconds. Bootstrap duration depends on API latency and universe size;
readiness is measured per symbol rather than promised after a fixed wait.

Up to six existing deep-scan slots are reserved for fresh early/confirmed
observations from the last five minutes. Only verified eligible instruments
can receive priority. Core direction, structure, derivatives and execution
rules still independently decide whether a prioritized instrument qualifies.

## Frozen observational rules

`EARLY_BREAKOUT_V1_CONFIG.json` fixes the version and a SHA-256 config hash.
Values reuse the existing early/structure defaults; they are hypotheses for
forward validation, not calibrated probabilities or promises of profitability.
Changes belong in a separate version rather than editing this test in place.

The reference is six prior closed 5m candles, excluding the evaluated candle.
Preparation requires compression and approach to its high/low. Early LONG
requires upward EMA7 slope, accelerated closed-1m quote volume, buy-side taker
flow and proximity to the upper boundary; SHORT uses mirrored inequalities.
An episode locks its level and opposite box boundary. There are extension/ATR
and known-next-level anti-chase checks. Unknown next resistance/support remains
explicitly unknown; an entry, stop or target is never invented by this layer.

States are NONE, PREPARING, EARLY, CONFIRMED, CHASE, BROKEN and EXPIRED.
PREPARING/NONE stay silent. CONFIRMED requires a newly closed 5m candle after
the first early observation, correct direction, body, rejection wick and
volume checks. It means candle confirmation only, **not a trade signal**.
Two post-signal closed 1m candles back inside the level, or loss of the opposite
boundary, invalidate the observation. Episodes expire after three hours and
use a one-hour per-direction cooldown. First prices/times remain immutable.

## One Telegram message per coin

The early worker and existing core use a persistent shared message-card DB.
The first applicable observation creates a message; later state changes edit
its stored message ID. The exact core text remains in the same card, with its
message time; stale core sections expire on early updates. An old queued watch
cannot overwrite a recent actual trade signal. Opposite early observations
cannot be presented as confirmation of the core's opposite direction.

Early transitions bypass the old twenty-minute observational queue. At most
three cards are processed per poll. Failed updates are retried; superseded or
stale initial early transitions are not delivered as new opportunities.
Acknowledged IDs persist across restarts. Initial network delivery whose ACK
is lost is explicitly UNCERTAIN and held for review: the API has no initial
send idempotency key, so blindly resending cannot guarantee one message.
Edits do not create a new Telegram notification. No message is automatically
deleted/recreated when Telegram refuses an edit.

## Forward records

SQLite retains all ready-symbol LONG and SHORT observations, including NONE,
weak preparations, chased moves and failed episodes. Missing-data cases remain
separate. Transition records include delivery status and config hash. Episodes
record preparation/first-anomaly, first early and confirmation times/prices,
gain before the signal and gain before confirmation.

15m/1h/3h raw post-observation outcomes use complete 1m paths, with +3/+5/+7/
10/+15% reach flags and time-to-reach upper bounds, MFE, MAE and final return.
The partially elapsed signal minute is excluded to avoid counting pre-alert
extremes, so its post-alert portion is conservatively unmeasured. Missing paths
remain DATA_PENDING and are retried. These are observational price outcomes,
not executable net trade returns; the existing core's cost-aware trade tracker
continues to own actual paper-trade labeling. This version has no proven edge.

## Enable and validate

Enable `LS_EARLY_BREAKOUT_V1_ENABLED=1` on the existing native-chain motor.
The daemon enables `LS_UNIFIED_CARD_ENABLED=1` and supervises the new worker.
The existing private history relay URL and persistent volume are required.
Defaults keep the new worker disabled until intentionally enabled.

Check `EARLY_UNIVERSE_HEALTH`: eligible vs ready/invalid, two feed connection
states, active stages, delivery statuses and config hash. Check
`EARLY_OBSERVER_SCAN_PRIORITY` for priority handoff. Compare first early times
and later paths across the full universe; retain false positives and missing
outcomes. Do not claim that every sudden move can or should produce an alert.

To disable this observational version, set both flags to 0. This stops the new
worker/transport routing without deleting its records or changing core rules.
