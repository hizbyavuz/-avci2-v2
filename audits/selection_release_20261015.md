# Candidate selection repair — V3.3

Live code commit: 6902e0ff98ca3667b7342601aa0b44066b32770b
Deployment: cf5147dc-a559-4ed6-a27c-34d630fe506f

## Observed problem
The pre-change 78-scan window contained no eligible plans. No candidate reached the notification stage. The bounded rejection review measured 309 observations; 116 were NO_DEFINED_SETUP. These are correlated observations, not independent trades. Future returns in that review do not prove a usable entry or profit.

## Changes
- Evaluate all existing recognized hypotheses through the same admission checks. Previously the first detected pattern prevented examination of the second, even when the first failed geometry/freshness.
- Prefer valid READY over valid WATCH, retaining declared model order within each stage.
- Add SWING_BREAKOUT: last recent confirmed swing high followed by confirmed higher low (symmetrically lower high for SHORT), no prior closed break, no subsequent violation of that low, and a direction-aligned current closed candle. A fresh close beyond the swing is required for READY. WATCH requires the existing proximity and geometry rules.
- Keep the original cost assumption, minimum net payoff, anti-chase, liquidity, multi-timeframe context, reversal flow, and final path verification checks.
- Record per-pattern rejection reasons and alternate admissions in each scan summary. New strategy identity isolates changed rules from previous performance.

## Verification
655 unit tests passed. Added selection fallback, READY priority, real confirmed-pivot LONG/SHORT mirroring, repeated break and invalidated structure rejection, and common cost/chase gates.
Deployment reached SUCCESS. Runtime observations are recorded separately.

## Limits
The masking defect is demonstrated in code/tests; its market frequency is not established. The new swing model is an explicit uncalibrated hypothesis, not a proven profitable system. No performance claim can be made from unit tests or a few live scans. No automatic orders.
