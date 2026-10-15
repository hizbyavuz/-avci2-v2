# Local geometry and continuous candidate tracking

Authorized 2026-10-15 04:39 Europe/Istanbul. Parent: a59e0af7f677d8cfc238dad7a9379bb760e337b3.

This is a new strategy cohort, not a retrospective correction of earlier results.
Identity: LS_LOCAL_GEOMETRY_CONTINUITY_V2_2026_10_15.

## Confirmed defects

- The latest rotating scan was treated as the complete watch universe. An
  unscanned candidate was paused even if its live confirmation remained valid.
- V3 joined overlapping price intervals transitively, across support/resistance
  and 5m through 4h. This could move a local entry several percent, keep its old
  stop and enlarge the retest range until a remote target appeared reachable.

## Changes

- Pin watch selection and reviewed-symbol membership to the same committed scan.
- Distinguish an explicit failed review/capacity exclusion from absence from a
  rotating scan. Retain only previously active actionable plans with an unexpired
  review deadline. Deadlines derive from the actual analysis timestamp, not the
  restart/refresh time. Repeated absence cannot extend them.
- Reuse the existing 900-second grace duration as the maximum review age.
  Confirmation checks this deadline on every poll, even if the analyst stops.
  Radar/downgraded candidates remain paused; existing invalidation/3h expiry,
  fresh candle, derivatives and executable net-R controls remain enforced.
- Give active plans older than one scan interval first access to the existing
  six reanalysis priority slots (oldest first). Neither scan nor deep-scan caps
  increase. Priority is scheduling, never entry authorization. Capacity is still
  finite; this does not promise immediate full-universe deep analysis.
- Structural V4 selects the nearest confirmed same-side 5m/15m pivot within the
  existing near-level tolerance. Confluence does not move its boundaries.
  Entry clears that single local band. Stop is unchanged, retest absolute width
  is preserved, and level ordering is revalidated together.
- All-timeframe same-side opposing pivots remain obstacles. A zone overlapping
  entry fails closed, rather than pushing the entry to its far edge. A close
  target cannot be skipped to manufacture R. Existing cost/room/R thresholds
  are unchanged. No target or win-rate fitting was performed.
- V3's implementation and tests remain unchanged. Each new closed-candle scan
  computes V3 and V4 geometry from identical zones and stores a paired comparison
  in the analysis payload and STRUCTURAL_PLAN_V4 log. It is NOT a paired PnL
  backtest: old entry state transitions are not replayed in a second live engine.
- Existing ledger/rejection readers retain their historical structural_plan_v3
  field name; its explicit version and config hash now identify V4. Code/config
  hashes separate all forward results. No existing outcome is overwritten.

## Verification

519 tests pass in an isolated checkout (7.639 seconds), including 17 new tests.
Tests cover both directions, unchanged stop/retest width, chained overlaps,
high-timeframe obstacles, invalid inputs, scale invariance, paired baseline,
rotation continuity, explicit rejection, expiry without a new scan, restart,
priority scheduling and invalidation precedence. Existing atomic event/outbox,
single-delivery and no-open-trade-rearm tests also pass.

Captured PUMPUSDT geometry, 2026-10-15 01:20 UTC:

| Quantity | V3 | V4 |
|---|---:|---:|
| Original local trigger | 0.0047400000 | 0.0047400000 |
| Rebuilt SHORT trigger | 0.0045946571 | 0.0047267714 |
| Retest width / rebuilt entry | 3.26657% | 0.10039% |
| Geometry decision | Qualified | ENTRY_INSIDE_OPPOSING_ZONE |

The captured fixture contains the old trigger and target components only, not
the complete candle universe. It proves the reproduced entry/overlap behavior,
not improved profitability or a missed executable trade. The nearby support is
no longer hidden inside an enormous merged band.

## Deployment and rollback

Target only long-short-motor, project ba2b84a7-22c6-4703-aefc-c6f95393c1b6,
service caad3157-e67b-4d20-80d9-67aa5fe8778c, production
5fc78d8b-4f54-4b50-867d-c544cbd74aaa. Pin to the commit containing this document.
Require Railway SUCCESS plus LS_STRATEGY_EFFECTIVE, STRUCTURAL_PLAN_V4 and
LIVE_TRACKING_CONTINUITY runtime evidence. Do not send synthetic trade alerts.
Rollback source pin: a59e0af7f677d8cfc238dad7a9379bb760e337b3; the additional
nullable SQLite deadline column is backward compatible. No storage services,
paid resources, other engines, or automatic order execution are added.

Successful deployment proves operational behavior only. Positive after-cost
expectancy remains unproven and must be evaluated from the new cohort's actual
forward signals, failures and unresolved data cases.
