# AVCI Holdout Intervention Protocol — 2026-09-30

This protocol governs bugs, API changes, workflow failures and infrastructure incidents during the prospective holdout beginning 2026-10-07T00:00:00Z.

## Core rule
A holdout incident may be repaired in-place only if the change cannot alter:
- candidate/control membership;
- feature values used by protected selection;
- signal/event timestamps;
- labels, targets, stops, horizons or first-touch ordering;
- entry delay or execution-cost assumptions used by the confirmatory result;
- security eligibility;
- BTC-DOWN regime assignment;
- the canonical primary endpoint, matching logic or inference rule.

If any of the above can change, the current cohort is not repaired in place. A new version/cohort and new prospective start date are required.

## Allowed in-place fixes
Examples:
- provider URL/header changes that return semantically identical fields;
- retry/backoff changes that do not alter the accepted data timestamp/window;
- Telegram formatting/deduplication;
- artifact retention, backup or restore fixes;
- CI/test fixes that only update expected hashes/paths after an already-documented pre-holdout reseal;
- dependency/security patch only when replay/golden-master checks show identical protected outputs;
- workflow syntax fixes that do not alter step ordering or research semantics.

## Not allowed in-place
Examples:
- changing thresholds, weights, exclusions or candidate caps;
- changing which history is available to a feature;
- changing control matching or candidate/control eligibility;
- changing target/stop/horizon;
- changing a cost/slippage model used by the primary result;
- changing regime definitions;
- adding a new signal feature to protected selection;
- replacing missing data with synthetic/imputed values;
- changing unresolved/data-failure treatment.

## Required incident log
Every intervention must record:
- incident_id;
- discovered_at_utc;
- affected workflow/provider/file;
- first_bad_scan_utc and last_bad_scan_utc;
- failure mode;
- whether protected outputs could have changed;
- exact code/config diff;
- commit SHA;
- before/after replay result;
- affected scans/events;
- disposition: VALID / INVALID_DATA_WINDOW / NEW_VERSION_REQUIRED.

The log is append-only in `holdout_interventions.jsonl`.

## Affected-period rule
If data integrity is uncertain, scans/events from the uncertain interval are marked INVALID_DATA_WINDOW and excluded from primary statistics. They are not silently converted to wins or losses. Their frequency remains reported in the data-failure rate and pessimistic sensitivity analysis.

## Replay requirement
For an allowed fix, rerun identical archived raw snapshots through pre-fix and post-fix code where possible. Protected candidate/control membership and confirmatory labels must be identical. If equivalence cannot be demonstrated, classify the interval as invalid or start a new version.

## Provider/API schema change
Fail closed when a required source field changes meaning, disappears, or cannot be timestamp-aligned. Do not map a new field into an old protected feature without a new prospective version unless equivalence is mechanically demonstrated.

## Dependency/runtime change
Protected workflows use the environment lock. A dependency/runtime change requires:
1. explicit incident log;
2. regression suite;
3. archived-snapshot replay/golden-master comparison;
4. identical protected outputs.
Otherwise a new prospective version is required.

## Holdout extension
There is no extension option for this version. At 120 days, insufficient evidence => INCONCLUSIVE_RESTART_VERSION. Continuing beyond 120 days for a confirmatory claim requires a newly pre-registered version before examining the extension outcomes.

## Human decisions / tiny-live
Manual trades and future tiny-live execution evidence are recorded separately from system selection. They do not alter the frozen candidate cohort. Real fills may be collected as execution evidence, but may not be used to retune the current holdout.

## Principle
When uncertain whether a fix is semantic, treat it as semantic until equivalence is demonstrated.
