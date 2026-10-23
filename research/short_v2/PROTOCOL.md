# SHORT V2: failed auction and failed support retest

Recorded 2026-10-22 after diagnosing V1 and BEFORE evaluating these new models.
User instruction: fix SHORT before activating notifications. No deployment or
Telegram changes are authorized by a positive result on already examined data
alone. This experiment leaves the frozen LONG and V1 outcomes unchanged.

## Question

V1 sells the loss of a 72-hour low. Of 157 measured August shorts, 131 stopped,
only 27 reached 2 initial R in-plan, and only four stopped in the first four
hours. Those observations do not establish that tight early stops are the main
problem. Test a different entry location; do not widen stops to manufacture wins.

## Two fixed hypotheses, one configuration each

Common: 169 consecutive closed hourly candles, quote turnover >=2M USDT over
24h, positive volume baseline, last three hours' quote-weighted taker buy share
below 0.5, bearish confirmation candle. Price and flow come from USD-M only.
No arbitrary OI/account-ratio score. OI/funding remain available as research
context; their previously failed AND filter is not silently reused.

1. FAILED_BREAKOUT_SHORT: an hourly close breaks the preceding 72-hour high,
   within the past 24 hours. Within six hours it closes back under the frozen
   level. A subsequent candle (after re-entry, before confirmation) retests that
   level within 0.25 signal-hour ATR but closes below. Every close from re-entry
   through confirmation must remain below the level. Confirmation closes below
   that retest candle's low, with the previous close at/above the low. Stop is
   the highest price from breakout through confirmation plus 0.25 ATR. The prior
   range floor remains a known downside obstacle, alongside untouched 4h pivots.
2. BEAR_RETEST_SHORT: an hourly close breaks the preceding 24-hour low, within
   the past 24 hours. At the break, two already confirmed hourly pivot highs in
   the preceding 72h must be descending. After the break, a candle retests the
   frozen broken support within 0.25 signal-hour ATR, closes below, and all later
   closes remain below. Confirmation and stop rules are as above, except the stop
   anchors the highest price from retest through confirmation plus 0.25 ATR.
   Retest must be within the last 12 hours. A flat drop without a retest is rejected.

All prices used for the decision are known at the confirmation close. Geometry:
entry at/below retest low, no more than 1 ATR below it; risk positive and <=12%.
If a known support is present, room after 0.30% costs must be >=2 initial R.
Otherwise room is unknown, never infinite or an estimated success probability.
Observed move from the episode high must be <=25%. No target is moved to improve
hit rate. Each symbol/model is suppressed until six consecutive closed hours
back above the previously frozen trigger, matching the existing episode concept.

## Fixed execution and reporting

Original V1 exit is unchanged: after +2R arm a completed-4h structural trail,
at most 168h, worst open through stop, stop first within a minute, 0.30% fee/slip.
Entry is the first FULL minute after decision (confirmation close +1000ms).
This experiment changes selection only. Record missing minute entry/path rather
than substituting an hourly close or accepting only winners. If a stop closes a
plan before a later data gap, realized fee-only plan result can be known; raw
168h coverage stays separate. Funding without certified coverage stays unknown.

Every model runs across all officially verified cached August and September
hourly archives, not just old signals. Both months were already viewed and are
development data. Report both models, all rejected/unknown/invalid cases, monthly
results, in-plan excursions, holding times and source hash coverage. Cached
minute data are uneven across symbols/months: conditional estimates are not an
unbiased strategy sample. No network attempt or alternative route is needed.

Preregistered continuation criteria: a hypothesis is NOT eligible for activation
if fee-only expectancy is nonpositive in either measured month, if known complete
cost coverage is absent, or if independent untouched evaluation is absent. A
positive convenience-sample mean alone cannot mark it validated. At least a
same-exit simple baseline and complete point-in-time crypto universe are needed
before making a claim of superiority. No threshold search after these results.

Output labels: research_only=true, actionable=false, win_probability=null.
The scope is a specific SHORT redesign and measurement; it is not a guarantee
that a profitable strategy exists or can be finished in one historical pass.
