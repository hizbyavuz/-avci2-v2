# AVCI Confirmatory Pre-Registration Supplement — 2026-09-30

**Canonical confirmatory contract:** `final_test_hypothesis_20261007.json`

This note is explanatory only. If any sentence here conflicts with the JSON contract, the JSON contract wins.

## What happens on 7 October 2026?
At **2026-10-07 00:00 UTC (03:00 Europe/Istanbul)** the prospective untouched holdout begins. Nothing starts trading automatically. From that timestamp onward, new observations are treated as protected FINAL_TEST evidence. Scanner rules, thresholds, features, candidate membership and confirmatory analysis definitions may not be tuned from those outcomes.

## What is the single primary confirmatory test?
The pre-registered primary hypothesis remains:

> In BTC-DOWN episodes, frozen Avci candidates outperform same-scan matched controls on conditional-BTC-beta-residual, 2x transaction-cost-stressed, 72-hour fixed-exit net return.

The unit of inference is the **72-hour overlapping market episode**, not each coin event.

The primary comparison is candidate minus same-scan matched-control return.

## What is secondary?
The following remain useful but cannot replace the primary confirmatory result:
- +3/+5/+7/+10/+15 barrier hit rates and hit times;
- +10 target / -7 stop first-touch outcomes;
- MFE/MAE;
- other BTC regimes;
- Telegram/PAPER_ELIGIBLE outcomes;
- feature combinations;
- Gate weighted discovery;
- New Launch.

The Binance frozen outcome engine still records **+10 target / -7 stop / 72h** as its main operational barrier result, but that operational label is not allowed to silently replace the registered final-test hypothesis above.

## Engine separation
- Binance, Gate and New Launch are not pooled into one success claim.
- New Launch is observational and has no claim on this holdout.
- Gate results are reported separately; they cannot rescue or replace the registered primary hypothesis.
- A future Gate/New Launch confirmatory claim needs its own prospective version.

## Sequential testing
Registered N_eff checkpoints remain 50/100/200.
- 50 is diagnostic only.
- The primary family uses alpha 0.025.
- Three registered looks use conservative Bonferroni allocation: alpha <= 0.00833 per look.
- Statistical significance alone does not authorize live trading.

## Existing go-live gates still apply
A tiny-live review also needs the existing governance requirements, including at least:
- 60 calendar days;
- 150 closed candidates;
- N_eff >= 100;
- positive net expectancy and benchmark excess;
- PF / drawdown / execution / data-quality gates;
- candidate-control evidence;
- pessimistic unresolved/data-failure sensitivity.

Passing never enables automatic execution.

## Execution evidence
Evidence hierarchy:
1. REALIZED_FILL
2. BARRIER_TIME_QUOTE
3. SIGNAL_TIME_QUOTE_OR_BOOK
4. MODEL_ONLY
5. MISSING

Quote is not fill. Missing fills are not imputed. The write-only `strict_execution_evidence.py` ledger records this distinction without changing frozen returns or candidate selection.

## Data failures
Primary results must be accompanied by a pessimistic sensitivity treating unresolved/data-failure candidate outcomes as stop/loss. If that pessimistic candidate-control expectancy is <= 0, tiny-live review is blocked.

## Metric clarifications
- Event-series Sharpe/Sortino with sqrt(365) are diagnostic only.
- Time-normalized portfolio returns are required for the go-live Sharpe/Sortino interpretation.
- Timeout returns stay in expectancy.
- MAE diagnostic adverse excursion should not be reported positive when there was no adverse excursion.
- Canonical primary bootstrap count is 2000.

## Holdout rule
After 2026-10-07T00:00:00Z, changing the canonical hypothesis or the frozen selection/label/execution semantics contaminates the cohort. The remedy is a new version and a new prospective start date, not an in-place repair.
