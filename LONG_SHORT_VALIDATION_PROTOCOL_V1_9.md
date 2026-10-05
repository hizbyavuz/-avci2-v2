# Long/Short V1.9 Statistical Validation Protocol

Status: FROZEN OBSERVATIONAL PROTOCOL  
Effective date: 2026-10-05  
Applies to: LSA_V1_9_CLOSED_CANDLE_PARALLEL_VALIDATION_2026-10-05

## 1. Freeze rule

The live decision rules are frozen during this validation period. Do not change score weights, 55/12 decision thresholds, RSI bands, HTF gates, early-entry thresholds, stop/target formulas, or Telegram decision states inside V1.9. Any such change creates a new version and a new validation cohort. Historical V1.9 observations remain attached to V1.9.

## 2. Primary question

There is exactly one confirmatory primary hypothesis:

> Does a TRIGGERED continuation signal produce positive 60-minute net expectancy after realistic execution costs, and does it outperform a same-scan matched non-shortlisted control?

Primary stage: TRIGGERED  
Primary horizon: 60 minutes  
Primary outcome: net R multiple  
Primary comparison: matched CONTROL_NOT_SHORTLISTED delta R  
Primary confirmatory data cohort: BINANCE_FUTURES_NATIVE  
Primary independence unit: 2-hour market episode cluster + direction  
Primary confidence interval: deterministic episode-cluster bootstrap, 95% CI
Primary actionable cohort: Telegram delivery timestamp must exist. Model events that were not delivered are retained as operational/research observations but cannot prove user-executable edge.

Everything else is exploratory and must not be used to declare V1.9 successful.

## 3. Primary evidence rule

No production-edge claim before at least 100 independent primary episode clusters. 200+ episode clusters is the preferred serious-evidence target.

At or above the minimum sample:
- primary net expectancy R must be > 0;
- mean matched-control delta R must be > 0;
- the lower bound of the 95% episode-cluster bootstrap CI for matched-control delta R must be > 0.

Before the minimum sample size, all conclusions are INSUFFICIENT_EVIDENCE regardless of point estimate.

## 4. Execution shadow

Primary execution is not filled at the theoretical signal price.

Execution timestamp:
max(condition_time, telegram_sent_time when present) + 30 seconds human-decision delay.

Entry:
first available 1-minute market proxy after the execution timestamp.

Costs:
- fee: 5 bps per side;
- minimum slippage: 10 bps per side;
- use the larger of minimum slippage, analyst order-book execution proxy, and a small volatility buffer derived from the execution minute;
- exit is also cost-adjusted.

This execution model is observational only. It does not change Telegram messages or live signal states.

CLOSE_CONFIRMED / retest ideas are not assumed filled merely because price touched a retest zone. Until an explicit fill model is frozen, they remain exploratory. The confirmatory primary endpoint uses TRIGGERED and a delayed market-execution proxy, avoiding optimistic "touched = filled" accounting.

## 5. Matched controls

For each primary TRIGGERED event, controls are selected from the same analyst scan:
- not shortlisted;
- same direction hint as the signal direction;
- closest available observations by liquidity, absolute 24h move, ATR%, and prefilter rank;
- maximum 3 controls.

Controls are evaluated from the same execution timestamp and same 60-minute horizon. This prevents the primary comparison from using a different market moment.

## 6. Data-mode cohorts

Never pool native and fallback market modes into one headline statistic.

Required cohorts:
- BINANCE_FUTURES_NATIVE
- SPOT_PLUS_BYBIT
- SPOT_PLUS_GATE
- SPOT_PLUS_OTHER_OR_PARTIAL
- UNKNOWN

The primary report must show cohort counts and expectancy separately. Mixed-cohort headline results are descriptive only. The confirmatory PASS/NOT_CONFIRMED decision uses BINANCE_FUTURES_NATIVE only. Fallback cohorts remain exploratory unless a future protocol preregisters a separate primary test.

## 7. Episode definition

The legacy 30-minute episode ID remains untouched for backwards compatibility.

The confirmatory research layer uses a more conservative independent cluster:
floor(signal_time / 2 hours) + direction

BTC regime is deliberately not used to split the episode further, to avoid inflating effective sample size.

## 8. Score calibration

Score calibration is exploratory.

Buckets:
- 55-64
- 65-74
- 75+

For 60-minute outcomes, report:
- count;
- independent episode count;
- mean net R;
- 95% episode bootstrap CI;
- win rate.

A useful score should be approximately monotonic: higher buckets should not repeatedly perform worse than lower buckets. No live threshold is changed during V1.9 based on interim calibration.

## 9. Multiple-comparison control

EARLY, CLOSE_CONFIRMED, 15m, 240m, ablations, reversal, score buckets, and cohort breakdowns are exploratory.

They may generate hypotheses for V2, but they cannot replace the frozen primary metric after results are seen.

## 10. Reversal conflict

Continuation and reversal are separate hypotheses. Reversal remains observational by default. If the same symbol has opposite continuation and reversal directions in the same research window, record a CONFLICT tag. Do not silently count both as independent confirmations.

## 11. Data integrity

Missing data is missing, not neutral.

Do not manufacture a complete derivative bundle by combining semantically different fields from different venues. Provider/source/quality must be retained with every research observation.

## 12. Reporting discipline

Every report must include:
- live version;
- protocol version;
- config hash when available;
- raw event count;
- independent episode count;
- data cohort;
- point estimate;
- 95% cluster-bootstrap CI;
- matched-control delta;
- evidence status.

No threshold optimization is allowed inside this frozen V1.9 cohort.


## 13. Config-hash isolation

Every post-freeze analyst event is stamped with the SHA-256 hash of `LONG_SHORT_V1_9_FROZEN_CONFIG.json`.

Confirmatory evidence must match the currently frozen live-config hash. Native-Futures events with a missing or different config hash are retained for audit/history but excluded from the confirmatory sample. A config change therefore cannot silently inherit V1.9 evidence.
