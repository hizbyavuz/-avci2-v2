# Long/Short Structure Gate V2

Status: ACTIVE LIVE-ALERT LAYER  
Effective date: 2026-10-06  
Base model: frozen V1.9

This layer exists because the frozen V1.9 score can identify directional setups without fully reproducing the visual checks used during manual chart review.

It does **not** rewrite V1.9 scores or historical V1.9 paper results. It creates a new live-alert cohort.

## What V2 checks

1. **Support/resistance as zones, not a single arbitrary line.**
   Closed-candle pivots from 5m, 15m, 30m and 1h are clustered into price bands. Repeated touches and agreement across timeframes strengthen a level.

2. **Room to the next obstacle.**
   A LONG must have enough room before the next meaningful resistance/target. A SHORT must have enough room before the next meaningful support/target. Minimum live precheck room is 0.35%.

3. **Volume/candle agreement on the confirming 5m close.**
   The confirming candle needs at least 1.10x recent quote volume, body/range >= 0.45, a directional close, a close in the correct part of the candle, and limited rejection wick.

4. **Fake-breakout rejection.**
   A wick through the trigger followed by a close back inside the old level is rejected and cannot become CLOSE_CONFIRMED.

## Telegram behavior

A candidate can only enter the live watchlist when the structural zone precheck passes. A 5m close becomes `CLOSE_CONFIRMED` only when the live volume/candle/fake-breakout checks also pass. `TRIGGERED` still requires the existing retest sequence after confirmation.

Thresholds are versioned in `LONG_SHORT_STRUCTURE_GATE_V2_CONFIG.json`. Future changes must create a new cohort/version instead of rewriting past V2 evidence.
