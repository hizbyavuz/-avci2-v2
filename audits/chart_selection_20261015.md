# Saved chart decisions and subsequent movement — 15 October 2026

Read-only audit ran at 16:08 UTC (19:08 Turkey), deployment `53c6cf15-6d24-429d-86e1-9e3541811929`, source `e939943eb7482badc4981afb28046b481cf9e8c1` (SUCCESS). Entry rules and strategy identity were unchanged. The audit's 19 related tests passed.

239 saved observation rows had complete forward paths; none were unresolved in this bounded sample. These are correlated observations, not independent trades. Only current-strategy records with valid reference prices and no initial data/liquidity failure were considered. Each measurement ends at the first full minute close at or after 15 minutes; effective durations vary slightly. Intraminute extrema omit the partial observation minute. Returns below are raw price changes, not executable profit, and no stop/target success is inferred.

| Symbol | Original Turkey time | Saved decision | Later endpoint change | Best directional excursion |
|---|---|---|---:|---:|
| SIRENUSDT | 18:35:21 | LONG rejected: net reward below structural risk | +8.38% | +9.86% |
| GRASSUSDT | 18:45:14 | LONG rejected: opposing 4h context | +3.22% | +3.95% |
| PYTHUSDT | 18:33:48 | LONG rejected: no defined setup | +1.74% | +2.30% |
| AKEUSDT | 18:35:21 | LONG rejected: net reward below structural risk | -2.06% | +4.63% |

The countertrend LONG group contained 98 observations from 49 symbols: mean signed endpoint change +0.3245%, 44 endpoints above +0.30%, and 42 with at least +1% favorable complete-minute excursion. This small, selected window does not prove a profitable reversal strategy. Cost/obstacle filters also rejected losing paths: the long `OBSTACLE_WITHIN_COST_ALLOWANCE` group averaged -0.7604%, and the short `NET_REWARD_BELOW_STRUCTURAL_RISK` group averaged -0.4612% in the proposed direction. Do not remove all filters based on retrospective winners.

Telegram acknowledged fresh `sendMessage` calls for XRP LONG WATCH at 18:40:34 and READY at 18:41:06. ACK proves server acceptance, not a phone push notification. The READY observation's next roughly 15-minute price change was -0.1714%; profitability is not established.

ZEREBRO was assessed READY at 18:45:45 but the scan logged created=0 and delivered=0. Its assessed price was 0.051863 and allowed entry ceiling only 0.05187165; live-price revalidation, existing episode/cooldown, or another final gate could suppress it. Existing logs do not record the precise final suppression reason, so this is an observability gap, not a confirmed erroneous block. No rule was relaxed to force a notification.

The evidence supports investigating missed opportunities and the distinction between trend continuation and reversal. It does not establish that any individual rejection was wrong given its ex-ante stop, obstacle and entry geometry. Full measurement is preserved in `chart_selection_20261015.json`.
