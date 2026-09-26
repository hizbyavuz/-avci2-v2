# Avcı Independent Audit Package

## Purpose
This package is for an external reviewer with quantitative finance, statistics,
market microstructure, execution, or crypto-security experience. The objective
is not to approve the system. The objective is to identify reproducible flaws,
biases, leakage, invalid assumptions, weak controls, or operational risks that
the internal design may have missed.

## Canonical references
- Genesis freeze manifest: `genesis_freeze_manifest.json`
- Prospective untouched holdout start: **2026-10-07T00:00:00Z**
- Canonical pre-holdout baseline commit: **9d3c428c63b1282e97c7a22a3925d3d679a2c8e2**
- Validation contract: `research_validation_spec.json`
- Main validation engine: `research_validation_layer.py`
- Decision discipline: `research_decision_discipline.py`
- P0 governance: `research_p0_governance.py`
- Binance scanner: `binance_scanner.py`
- Gate scanner: `scanner.py`
- Gate yellow observational cohort: `gate_weighted_discovery.py`
- Execution evidence: `read_only_execution_collector.py`, `execution_fill_import.py`
- Security red-team: `security_redteam.py`, `external_security_replay.py`
- Human override ledger: `human_decision_ledger.py`

## System claim boundaries
The system does **not** currently claim a proven trading edge. Historical
development data influenced rule design, so pre-genesis results are not
untouched OOS. The prospective genesis period is the first protected test of
the frozen design. Gate weighted discovery is observational and must not be
merged into frozen V5 performance after outcomes are seen.

## Audit questions

### 1. Leakage / contamination
Check whether any feature, label, selection rule, control selection, security
filter, execution model, or reporting path can access information timestamped
after the signal decision. Inspect signal time, data-source time, entry delay,
outcome construction, backfills, and cached state.

### 2. Genesis contamination
Trace the origin of R1/R2/R3, wake-up, retention, persistence, trigger, climax,
weighted-discovery weights, and all hard thresholds. Identify any rule whose
value was selected after viewing historical winners or validation outcomes.
Confirm such rules are treated as discovery-derived rather than untouched.

### 3. Multiple testing / researcher degrees of freedom
Inventory every tested feature, threshold, ruleset, target, horizon, regime,
subgroup, and report. Verify that failed hypotheses remain visible and that
BH-FDR/checkpoint families are not narrowed after outcomes are observed.

### 4. Dependence / effective sample size
Check clustering by asset, day, week, market regime, sector/narrative, and
cross-venue duplicates. Challenge whether the current block/bootstrap design
still overstates independence.

### 5. Controls
Review candidate, near-miss, random, simple-baseline, twin, and shadow-universe
controls. Check matching quality, contamination, selection timing, and whether
controls are exposed to the same execution/cost/labeling rules as candidates.

### 6. Survivorship and delisting
Test whether delisted, suspended, illiquid, renamed, migrated, or data-missing
assets are omitted or silently converted into normal failures/successes. Verify
pessimistic sensitivity analysis.

### 7. Execution realism
Challenge entry delay, spread, slippage, price impact, funding, fees, DEX
sellability, MEV/sandwich buffer, failed transaction assumptions, gap stops,
same-candle ordering, and position-size dependence. Distinguish quotes from
real fills.

### 8. Security model
Review false-safe risk, unavailable providers, exact-contract identity,
Token-2022, tax/honeypot/proxy risks, LP protection, concentration, creator
history, wash/sybil proxies, and external security corpus transferability
between EVM and Solana.

### 9. Market-regime confounding
Check whether candidate performance is simply beta to BTC/SOL, sector momentum,
listing/news effects, volatility state, weekend effects, funding windows, or
liquidity regime.

### 10. Operational failure
Review API outages, stale timestamps, cache corruption, clock drift, partial
runs, overlapping workflows, retries, duplicate signals, cooldown logic,
Telegram/report failure, exchange outage, delisting, and source disagreement.

### 11. Statistical decision contract
Recompute expectancy, profit factor, Sharpe, Sortino, drawdown, confidence
intervals, calibration, FDR, bootstrap inference, and effective N. Check the
pre-registered stopping/checkpoint rules for optional-stopping loopholes.

### 12. Human intervention
Check whether manual trades, skipped trades, overrides, discretionary filters,
or post-hoc exclusions can contaminate system performance. System outcomes and
human outcomes must remain separate.

## Reproduction checklist
Run at minimum:

```bash
python genesis_freeze_guard.py
python -m unittest discover -s tests -v
python research_validation_layer.py binance
python research_validation_layer.py gate
python research_p0_governance.py binance
python research_p0_governance.py gate
python research_decision_discipline.py binance
python research_decision_discipline.py gate
```

If real fill exports are available, independently verify that the imported
fills match exchange/wallet records and were not manually filtered.

## Required audit output
For each finding, report:
- unique finding ID;
- affected file/function/table;
- exact reproduction steps;
- observed evidence;
- why it matters;
- severity: INFO / LOW / MEDIUM / HIGH / CRITICAL;
- whether it invalidates historical evidence, prospective evidence, execution,
  security, or operations;
- proposed test or remediation;
- whether remediation would require a new prospective version.

A finding must be reproducible or explicitly marked as a hypothesis requiring
additional evidence.

## Known limitations before external review
- Prospective untouched genesis has not yet matured.
- Real fill coverage is incomplete.
- Gate historical survivorship coverage is incomplete.
- Some providers can be missing or rate-limited.
- Historical provider latency cannot be reconstructed honestly.
- Security evidence is heterogeneous across chains.
- Small closed samples can produce unstable point estimates.
- A clean audit does not prove positive expectancy.

## Independence declaration requested from reviewer
The reviewer should disclose whether they helped design any current rule,
threshold, feature, label, or validation criterion. If yes, their review is
valuable but should not be described as fully independent.
