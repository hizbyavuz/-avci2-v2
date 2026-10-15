# Rejected-candidate audit V1

Protocol fixed before inspecting this audit's outcomes. Cutoff: 2026-10-15
03:46 Europe/Istanbul (00:46 UTC), as requested. Research only; production
scores, admission, entry confirmation, stops, targets and alerts are unchanged.
No orders or new Telegram notifications are introduced.

## Question and coverage

Did a recorded rejected/pending analysis already have a usable fixed plan whose
hypothetical early entry would have earned a net return? The population is the
existing observations table: selected deep analyses, partial-data research and
recorded data blocks. It is NOT the full Binance universe or a full-market
missed-mover recall estimate. Unselected coins and unrecorded failures are outside
coverage. Existing gross 15/30/60/180-minute movement labels remain separate.

## Point-in-time protocol

- Use only the original recorded setup direction, stop, TP1 and cost proxy.
  If the direction engine rejected direction, the original setup's proposed
  direction is a counterfactual, not a new accepted direction. No picking the
  direction from subsequent returns, inventing targets, or moving stops.
- Enter at the next full one-minute open after observation, even though the
  production trigger/confirmation/room/R gates have NOT passed. This is the
  explicit experimental intervention; it never authorizes a real trade.
- If stop/target do not surround that entry, record ENTRY_MISSED. If frozen
  direction/levels/cost are absent, retain NO_FROZEN_PLAN. No guessed defaults.
- Hold at most 180 minutes, with fixed TP1/SL; stop-first for unresolved
  intraminute ordering, first observed open for a gap stop. Do not count gains
  before hypothetical entry. Missing pre-exit candles/funding are unresolved;
  missing post-exit candles cannot erase an already resolved trade.
- Subtract original analyst round-trip cost proxy and verified historical
  funding. Costs are assumptions, NOT real book/fill evidence. Report additional
  0.3 and 0.7 percentage-point cost sensitivities; do not select the best case.
- Sample the first recorded observation per symbol/direction/strategy/data
  cohort, then wait at least 181 minutes to avoid overlapping 180-minute entries
  within that group. Raw observations and repeated scans remain untouched.
  Versions/data cohorts can overlap and must not be pooled as independent bets.
- Preserve strategy identity from the recorded structure gate when available;
  missing identity stays LEGACY_IDENTITY_UNAVAILABLE. New records also include
  explicit strategy identity and analyst status. Never relabel old data.

## Interpretation

Group direction/tradability rejects, structure rejects, precheck-passed but not
entry-confirmed, and data failures separately. A hypothetical profitable trade
in an illiquid/rejected coin is not proof that the real system should enter it.
TP hits with negative net return remain losses. Missing/no-plan cases are not
ordinary losses and are not quietly removed. The existing unresolved -1R
sensitivity is an assumption, not a worst-case gap-loss bound.

The normal signal ledger remains the actual-signal comparison source. These
populations are observational, not randomized causal groups. A larger sample,
market-regime segmentation, uncertainty and out-of-sample confirmation are
needed before changing production filters. Neither silence nor a few hypothetical
winners establishes an edge.

## Operation

The existing data-opportunity worker invokes the audit after its original
resolution pass, approximately once per minute plus processing time. New tables
and an ingestion cursor live in the same existing SQLite research database;
there is no duplicate raw market archive or new paid service. Per-observation
plans freeze once, terminal outcomes do not recompute. Bounded work batches and
oldest-checked ordering prevent unresolved paths starving newer observations.
REJECTION_AUDIT_SUMMARY logs contain coverage, states, cohort metrics and examples.
