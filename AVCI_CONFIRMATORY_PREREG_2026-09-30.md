# AVCI Confirmatory Pre-Registration Supplement — 2026-09-30

Status: PRE-HOLDOUT, immutable after 2026-10-07T00:00:00Z except by starting a new version/cohort.

This supplement clarifies the confirmatory endpoint without changing any scanner selection threshold, feature, security veto, candidate membership rule, entry delay, target, stop, or execution assumption.

## Scope
- BINANCE and GATE are separate confirmatory engines.
- NEW_LAUNCH is observational/exploratory only and cannot make either main engine pass.
- No pooled "global success" may substitute for an engine-specific pass.

## Prospective holdout
- Start: 2026-10-07T00:00:00Z.
- Pre-holdout data are discovery/calibration and are not untouched OOS evidence.
- Minimum calendar age before go-live review: 60 days.
- Maximum window: 120 days; insufficient evidence at that point => INCONCLUSIVE_RESTART_VERSION.
- Results may be monitored continuously, but no rule/threshold/feature may be selected from holdout results.

## Binance primary confirmatory endpoint
- Cohort: frozen CANDIDATE events only; controls are contemporaneous NEAR_MISS + RANDOM_CONTROL under the same outcome/cost rules.
- Entry: signal + 120 seconds, using the frozen executable-entry model.
- Primary target: +10%.
- Stop: -7%.
- Primary horizon: 72 hours.
- Path: first barrier; 1-minute path resolves same-candle ambiguity when available, otherwise stop-first.
- Primary return series: 72h primary-barrier net_return_pct after the frozen cost model.
- Primary contrast: mean(candidate net return) - mean(control net return).
- Required direction: > 0, with the pre-registered economic floor of +0.50 percentage point retained as a separate economic-materiality gate.
- Benchmark excess vs BTC remains required.

## Gate primary confirmatory endpoint
- Cohort: frozen V5 CANDIDATE events only. EXPANDED/YELLOW/WEIGHTED discovery remains exploratory unless separately pre-registered in a future version.
- Controls: contemporaneous NEAR_MISS + RANDOM_CONTROL exposed to the same 2-minute entry delay, barrier logic, horizon and cost accounting.
- Entry delay: 2 minutes.
- Primary target: +10%.
- Stop: -7%.
- Primary horizon: 72 hours.
- Path: first barrier; 1-minute path where available, otherwise stop-first.
- Primary return series: cost-adjusted validation net return. Quote-observed cost is not called realized cost.
- Primary contrast: mean(candidate net return) - mean(control net return).
- Required direction: > 0, with +0.50 percentage point retained as an economic-materiality gate.

## Sequential looks / multiplicity
- Effective-N checkpoints remain 50, 100 and 200.
- N_eff=50 is diagnostic only; it cannot authorize tiny-live review.
- A go-live review still requires all existing gates: >=60 calendar days, >=150 closed candidates, N_eff>=100, execution coverage, data-failure, drawdown, PF, Sharpe/Sortino and benchmark requirements.
- To avoid repeated-look inflation, confirmatory significance is evaluated conservatively per engine with family alpha 0.025 and three-look Bonferroni allocation: alpha <= 0.00833 at each registered checkpoint. Exploratory subgroups/targets/regimes never substitute for the primary endpoint.
- BH-FDR remains for exploratory checkpoint families; it does not replace the primary confirmatory rule above.

## Data failures / survivorship
- Unresolved, delisted, suspended or missing-data cases remain explicit.
- Primary statistics do not silently convert them to success or ordinary loss.
- A mandatory pessimistic sensitivity must also be reported with unresolved/data-failure candidate outcomes treated as stop/loss.
- Tiny-live review is blocked if the pessimistic candidate-control expectancy is <= 0.

## Execution evidence hierarchy
1. REALIZED_FILL — independently imported/read-only fill evidence.
2. BARRIER_TIME_QUOTE — executable quote captured at/near the barrier time.
3. SIGNAL_TIME_QUOTE / BOOK — observed but not barrier-time realized evidence.
4. MODEL_ONLY — assumed fee/slippage/network cost.
5. MISSING — no execution evidence; never imputed as realized.

Primary reports must show coverage by evidence grade. Quote-observed is never labeled realized.

## Readiness isolation
- trade_readiness and trader-message layers may use historical evidence for human-facing prioritization, but they do not redefine the frozen confirmatory CANDIDATE cohort.
- PAPER_ELIGIBLE/Telegram selection must not be substituted for the primary candidate cohort in the prospective holdout.

## New Launch
- NEW_LAUNCH is excluded from the Binance/Gate prospective confirmatory claim.
- Its thresholds, security guardrails and notification state belong to a separate observational cohort.
- Any future claim for New Launch requires its own prospective pre-registration.

## Metric interpretation clarifications
- Event-series Sharpe/Sortino annualized with sqrt(365) are diagnostic only unless computed from an explicitly time-normalized daily portfolio series.
- The go-live Sharpe/Sortino gate should be evaluated on the canonical time-normalized portfolio return series; event-series values cannot substitute.
- Timeout outcomes are included in return expectancy through their realized/close return; expectancy is not computed from only win/loss probabilities.
- MAE diagnostics should be interpreted as adverse excursion relative to entry; non-adverse paths should be floored at 0 adverse excursion in diagnostic reporting.
- Canonical bootstrap count for the primary validation report: 2000. Older modules using another count remain historical diagnostics, not the confirmatory implementation.

## No post-start edits
After 2026-10-07T00:00:00Z, changing this document or any rule it defines contaminates this cohort. A change requires a new version and a new prospective start date.
