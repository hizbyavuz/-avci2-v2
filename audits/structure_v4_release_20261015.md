# V4 structural hierarchy intervention

Production commit: b3e1ac44a3f9f0a3cd08bf6af720a9265f7fb7b5
Deployment: e7249dfc-b329-4506-a82f-5b63ff951bde
Strategy: 4161b777dd16199e864f294ffaeb3ee733f23c6d8ea12ddffbaae7b0b504d506
Previous live commit: 6902e0ff98ca3667b7342601aa0b44066b32770b

## Diagnosis
At 19:43:12 UTC V3.3 checked 171 symbols: 66 below liquidity floor, six missing verified history, two missing fresh quotes, 22 unclear hourly structures, 18 failed countertrend flow/price checks, 36 undefined setups, 17 insufficient room/net payoff, four extended entries. No eligible candidates, so Telegram delivery was not reached.

Microstructure and structural objective were conflated: every confirmed 5-minute swing could terminate a plan intended to be followed for up to three hours. With a 0.30% round-trip cost and minimum net 1R, required gross room is gross stop distance plus 0.60%. This is a declared selection policy, not a mathematical error. Applying it to tiny local hurdles makes many plans infeasible. A three-shape gate also excluded otherwise identifiable conditional level watches.

## Explicit policy change, not proven edge
- Retain daily/4h context, hourly direction, countertrend flow/price checks.
- Retain existing models; additionally allow a conditional watch at a recent confirmed unbroken local level with a confirmed structural invalidation.
- 5m controls timing. First confirmed 15m/1h/4h/daily obstacle supplies the structural objective. A nearer 5m hurdle stays visible in the user message; it may still prevent continuation.
- Retain stop, fees, minimum net payoff, anti-chase, data health, cooldown, closed confirmation and path controls.
- Same-tick V3 rule evaluation uses identical frames, quote, flow and observation time. It emits comparison counts only and never sends its own alerts.
- New version/hash; prior outcomes remain separate. No automatic orders.

## Verification
660 tests passed, including both directional mirrors, old-break rejection, invalidation rejection, common entry gates, explicit micro hurdle disclosure and no fabricated target when structural resistance/support is unavailable.
Railway reached SUCCESS. Runtime evidence is recorded separately.

## Limitations
This changes which level is considered the main objective, and therefore changes risk and expected holding path. It does not demonstrate positive expectancy, a win rate, or that any highlighted intermediate level will break. Many variants have been tried in the same session; ensuing performance is forward evidence, not an out-of-sample certification.

## First live result
19:48:31 UTC / 22:48:31 Turkey: Telegram acknowledged a new XRPUSDT LONG WATCH. Same-tick V3 comparison rejected XRP as NO_DEFINED_SETUP. V4's trigger 1.347, invalidation 1.3355878571428572, structural objective 1.3704 gave net reward/risk 1.25275 at the planned trigger under unchanged 0.30% assumed round-trip cost. Actual observed quote was 1.3439; the watch was not an executed trade or READY confirmation. Daily direction DOWN was disclosed. First complete scan: one eligible, one created, one delivered; no scan errors observed. No claim of predictive success follows from successful delivery.
