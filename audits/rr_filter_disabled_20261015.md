# V4.1 — user-requested minimum reward/risk filter disabled

Request: after discussing the fixed minimum 1:1 net reward/risk rule, user said "Kapatalım onu o halde". Scope was stated to user as disabling that admission rule, retaining cost accounting and structural/confirmation checks. User then explained that they enter with 30–40 dollars and exit at 10–15 dollars profit; margin/notional, leverage, and loss exit are not established. No signal thresholds were fitted to those amounts.

Changes:
- CONFIG.risk_reward_filter_enabled=False; stored on each decision.
- No minimum 1R rejection in scan or final lifecycle admission.
- Entry limit no longer embeds a hidden 1R boundary. It retains anti-chase and positive net room after the existing assumed cost.
- Costs, ratio and stop/target geometry remain calculated and recorded.
- V3 comparison retains its original 1R gate.
- Reliability reports assess the policy stored with each historical observation, rather than labeling all sub-1R new entries invalid.
- New V4.1 version/hash; previous observations preserved.

Validation: 664 tests passed. Tests exercise sub-1R scan and final admission for LONG/SHORT, retained cost gate, frozen comparison behavior and historical report policy separation. No automatic orders. Runtime verification is recorded separately. Increased admission does not establish positive expectancy.
