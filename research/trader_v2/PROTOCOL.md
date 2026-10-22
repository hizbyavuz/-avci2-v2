# Trader context V2 — recorded before context/outcome joins

2026-10-22. Existing SWING V1 source, thresholds and reports stay frozen.
The user explicitly authorized redesign, but did not authorize invented success
rates. No threshold is fitted to 60%, 70% or 100% wins. No automatic orders.

## What this experiment can answer

On all 625 August and 615 September V1 episodes, does adding price/market
context, executed flow and derivatives distinguish stronger from weaker plans?
This is an incremental experiment on the existing candidate generator, not a
claim that it covers every good trade. Both months have already been examined.
Neither is a new untouched holdout. September raw movement is not plan P&L.
Asset-class history is unresolved: archive results are not crypto-only.

## Fixed candidate variants (no parameter search)

1. BASE: unmodified V1 candidates and execution.
2. STRUCTURE: BASE plus aligned completed-4h return, aligned hourly return,
   and positive direction-adjusted BTC beta residual over the preceding 4h.
3. PARTICIPATION: STRUCTURE plus direction-aligned trailing 4h taker quote
   volume imbalance and strictly positive BASE-ASSET OI changes over 1h and 4h.
   OI is participation, not proof that new longs/shorts are the aggressors.
4. CONTEXT: PARTICIPATION plus the fixed crowding/carry constraints below.

OI declines do not automatically imply a reversal. Such candidates stay in
the baseline and are classified UNWIND; CONTEXT waits instead of changing side.
Global account ratio is not a ratio of long/short position notional. A ratio
above 2 for LONG (below 0.5 for SHORT), together with paid funding, marks a
crowded side and blocks this continuation hypothesis. These are inherited
candidate thresholds, not demonstrated predictive truths.

Funding uses the last *already settled* rate and that record's actual interval.
Its 72h flat-rate carry is a scenario, not a forecast. Positive expected receipts
are not used to improve R/R. Paid carry + 0.30% fee/slippage must not exceed
0.25 initial R. No fixed 8h assumption. Actual funding remains separate in P&L.
Proposed target is 2 initial R; it is a plan level, not a predicted price. If a
known obstacle is closer than this, the trade is rejected. Unknown resistance
does not mean unlimited space and is explicitly reported.

## Time and data contract

Only Binance USD-M data. No spot, other venue, proxy or alternate API route.
Official public archives may reconstruct historical features. Each ZIP requires
its official SHA256. Data sorted by event time; identical duplicates deduped,
conflicting duplicates rejected. Missing is never neutral/zero.

Hourly/4h/day candles must be closed. Beta uses hourly returns from the preceding
168h, ending BEFORE the 4h signal-return window. Funding settlement after the
decision cannot enter its features. Metrics are joined as-of with a conservative
5-minute delay; complete 5-minute OI windows are required. Archive reconstruction
does not establish live publication latency or historical tradability.

Every episode remains in the report: rejected, missing-context, invalid-entry,
open, closed and unknown full-net. Compare variants on the same eligible subset
as well as the full denominator. Report LONG/SHORT separately. Daily counts,
symbol concentration, mean/median R, positive and negative returns, and stop-
before-target counts are distinct. No claims based on raw post-exit rallies.
No selecting the best threshold after inspecting returns. Record failed variants.

## Promotion

This experiment cannot authorize live trading. A new strategy must have a frozen
entry/exit specification, complete funding costs, historical crypto membership,
execution constraints, a chronological evaluation with no overlapping outcome
leakage, same-plan controls and an unchanged forward paper period. Evidence must
be separate for LONG and SHORT. Missing or failed evidence leaves actionability
false. A high observed win rate alone never authorizes promotion.

## Primary research basis and its limits

- Liu & Tsyvinski, Risks and Returns of Cryptocurrency (2021): momentum evidence
  in its studied universe/horizons; does not prove our altcoin hourly signals.
  https://academic.oup.com/rfs/article-abstract/34/6/2689/5912024
- CME, Open Interest: open contracts and participation; not buy/sell direction.
  https://www.cmegroup.com/education/courses/introduction-to-futures/open-interest
- Binance market-data specification: OI quantity/value are different fields;
  global account ratio and position ratio differ; funding intervals can change.
  https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data
- Bailey et al., The Probability of Backtest Overfitting (2015): strategy search
  can overfit; all trials and previously viewed periods must be disclosed.
  https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf
- Binance public-data documentation: official archive schema and SHA256 checks.
  https://github.com/binance/binance-public-data
