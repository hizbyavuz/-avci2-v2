# V4.2 entry quality

V4.1 can confirm entries with very little space to the first structural obstacle
and opposing hourly trade pressure. V4.2 preserves broad WATCH discovery but
requires a fresh closed break, net reward/risk >= 1 to the original first target,
and a complete current rolling hour of aligned taker pressure and price response
before a new READY notification. The fixed round-trip cost assumption is 0.30%.
These thresholds are declared hypotheses, not fitted win probabilities.

Pending episodes use the current scan's flow and current price before confirmation.
Original trigger, stop and first target stay locked; a failed quality check remains
WATCH. Missing/stale flow cannot grant entry. Already delivered entries retain their
original stop/target lifecycle. WATCH messages explain outstanding quality conditions.
Their recorded reward/risk uses the expected entry, not a more favorable pre-trigger price.

New plan priority: READY first, then watches passing entry quality, then supporting
flow, then descending net reward/risk, then trigger proximity and symbol. The existing
three-new-plans-per-five-minutes budget stays in place. Active-plan and cooldown
checks precede the budget check so cap statistics no longer misclassify those cases.

Reports separate LONG and SHORT within each version, stage and horizon. Quality
blockers are retained with observations and deliveries. An acknowledgment-time price
change is recorded honestly even if it invalidates pre-send quality. Telegram delivery
is not an exchange fill, and the bot never submits exchange orders.

Validation: 88 chart tests, including mirrored LONG/SHORT rejection of weak payoff,
missing/stale/opposite flow, immutable levels, revalidation of pending confirmations,
all prices within the advertised READY range, queue ordering and side-separated results.
No future prices enter selection. No profitability claim or backtest pass is implied.

Release command: `python -u long_short_v42_stable.py`.
Rollback: pin `17a5681be9b67b651f42ca37caa8203578592f98` on
`release/chart-v41-stable-20261024`, and restore `python -u long_short_v41_stable.py`.
Retain the current volume and data relay. On version change, old episodes close through
the existing strategy-change lifecycle; their historical observations remain stored.
