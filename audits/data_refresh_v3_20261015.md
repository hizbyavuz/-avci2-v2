# Data freshness, geometry and rejected-candidate follow-through

Baseline production: 3633edfd01d13eeb6f6584070baf7b57336a347f (V4.1).

Observed bottlenecks: derivative demand capped before urgency ordering; a roughly
three-minute serial refresh cycle against a three-minute freshness limit; Unicode
contract names rejected by the relay and unescaped in archive URLs. Structural
rejections could retain the old target and room/R values despite a failed rebuild.

The new collector prioritizes current scans/live watches before the cap, replans
after a bounded work cycle, and starts refreshing after 60 seconds. Analyst/live
readers attempt demand recovery on invalid snapshots; shared SQLite leases avoid
duplicate upstream requests. Provider timestamps and 180-second validation remain
authoritative. Failed refreshes retain a 30-second backoff. Permanent missing core
fields still prevent trade signals; a data failure is not a trading verdict.

V5 is a new strategy hypothesis, not a profitability-proven correction: same-side
zones sharing the original local anchor form one region. Entry clears its outer
edge only within the existing nearby-level allowance; no transitive merging.
Independent obstacles remain blocking. Stop, retest width, cost, minimum room and
net-R thresholds are unchanged. Invalid geometry clears stale target metrics.
V4.1 remains unchanged and is evaluated on the same candles for comparison.

Rejected episodes report LONG/SHORT/NONE separately, retaining strategy identity,
reason, and 15/30/60/180-minute price outcomes. Gross direction accuracy is separate
from frozen-level hypothetical net PnL. Missing paths are unresolved, immature
paths pending, and NONE has no inferred direction accuracy. Summary episodes are
non-overlapping per coin/direction/cohort; raw observations remain stored. Ongoing
data gaps get observations every five minutes; scan-selection exclusions now get
forward observations as well. Historical missing records cannot be reconstructed
as if recorded contemporaneously. No real orders are enabled by this change.

Validation: Python unittest discovery, plus Unicode/invalid-path symbol cases in
Node. Runtime acceptance requires successful deployment, fresh urgent demand,
V4/V5 comparisons, and emitted direction reports; test success alone does not
establish future profitability or guarantee a signal.
