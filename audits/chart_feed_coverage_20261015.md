# Chart feed coverage repair — 2026-10-15

User reported no Telegram suggestions for one hour at 18:24 Turkey time.
Runtime logs from 14:24:15 to 15:25:58 UTC contain 121 completed chart scans,
zero eligible setups, zero created episodes and zero deliveries. The main
process and five collectors were alive; disk usage was about 18.4%.

At 15:25:58 the scanner checked 160 symbols, reporting 74 missing fresh prices,
33 opposing 4h context, 15 without a defined setup, 10 stale 5m tails, 9 unclear
hourly structures, 14 below the volume floor and 5 other rejections. These are
per-scan counts, not distinct missed trades. Missing-price checks preceded
liquidity checks, so some of the 74 were ineligible by volume anyway.

Root mismatch: the chart scanner used all eligible catalogue/ticker symbols,
while candle/price demand still used two legacy rotating windows with a 90
symbol cap. The history worker independently truncated the list to 90 as well.

Repair: in chart-only mode collect every symbol meeting the existing 25M volume
floor, plus active/recent delivered observations and BTC/ETH/SOL. Use up to
166 symbols (996 streams within the existing 1000-stream budget), preserve
active/outcome priority and explicitly log any capacity deferrals. History
repair uses that same bounded list without a second 90-symbol truncation.
Auxiliary derivative/taker queues retain their existing budgets. Low-volume
rejections are reported before missing-price rejections. No entry thresholds,
stop, target, cost assumptions or strategy setup rules are loosened.

The source identity changes because rejection-reason precedence changed; the
configuration hash is unchanged. Old cohorts remain stored and separate.
Regression checks cover >90-symbol coverage, history repair beyond symbol 90,
overflow disclosure, active reservations, liquidity reason attribution and the
existing runtime, stream, history, chart geometry and delivery tests.
86 tests passed. Live deployment and recovery must be checked separately;
this repair does not demonstrate profitable signals or guarantee a delivery.

## Live verification, 15:34 UTC / 18:34 Turkey

Deployment `3c4c6e69-da37-4e27-8ae4-734b6a99e857` reached SUCCESS with
commit `c9d818a539b35484b4897924262500092bb78662`. Runtime demand contained
99 chart-volume-eligible symbols and 100 total reserved symbols; all 100 were
subscribed (600 streams), with no capacity deferrals. One connection loss
during startup recovered automatically; the next coverage record showed the
same 100/100 coverage with zero write retries.

At 15:34:20, the completed 160-symbol scan had no FRESH_PRICE_UNAVAILABLE or
CHART_STALE_5m rejection. Rejections were 61 below volume floor, 54 opposing
4h direction, 22 no defined setup, 14 unclear hourly structure, 3 insufficient
net reward/risk, 4 obstacles within costs, 1 entry not near and 1 missing 4h
history. No eligible setup or new Telegram delivery occurred in that scan.
The one missing long-history case remains explicit; full data coverage or
strategy profitability is not claimed from this short operational check.
