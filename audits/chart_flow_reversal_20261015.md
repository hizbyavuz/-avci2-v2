# V3: hourly flow and countertrend notifications

User authorized live, explicitly labelled countertrend messages on 2026-10-15
19:25 Turkey time. Prior V2 source: e939943eb7482badc4981afb28046b481cf9e8c1.

V3 replaces the unconditional opposing-4h veto with an explicit reversal cohort.
The closed 1h structure must still select the direction. Twelve consecutive,
verified native 5m bars ending at the latest closed 5m boundary provide a rolling
60-minute window. Sum taker-buy quote volume / sum total quote volume (not an
average of ratios). LONG requires buy share > 50% and positive open-to-close
window return; SHORT requires buy share < 50% and negative window return.
These are declared directional-consistency hypotheses, not calibrated strength
thresholds or demonstrated profitability. Missing/invalid flow blocks only the
new reversal route. Continuation selection retains its previous rules.

Both existing 5m pattern models, live price health, immutable structural stops,
nearest obstacle across all timeframes, anti-chase checks, assumed 0.30% costs,
net reward/risk floor and notification cadence remain. WATCH is not an entry;
READY needs the existing closed-candle crossing plus current price holding the
trigger within the entry range. This does not add a separate retest requirement.
Pending READY rechecks current hourly flow when opposed to 4h. Auxiliary OI,
funding and account ratios remain supporting observations, not direction votes.

Messages show explicit countertrend warning, hourly buy/sell shares and matching
price change. Comparing the two half-hours flags weakening directional share
as context, without another veto. A dominant share does not imply price progress.
Flow is a closed rolling hour, not the currently forming 1h candle.

Version/config/source hashes separate V3 records. Reports split reversal from
continuation. Old V2 outcomes retain their partial-first-minute uncertainty
semantics; history is preserved. Existing episodes expire on strategy change.
No automated orders, new service, or additional long-running worker is added.

Validation: 47 chart/feed/audit tests pass, including mirrored LONG/SHORT,
weighted volume, excluded forming candles, gaps and corrupt/foreign flow,
price nonresponse, hourly ambiguity, anti-chase/geometry preservation, warning
rendering and pending READY revalidation. No return claim from ZAMA/BAT examples.
