# Implementation status — 22 October 2026

This supplements the frozen `research/swing_v1` work; it does not replace its
code, source hashes or outcomes. No deployment, Telegram send or order is made.

`long_short_trader_context.py` uses the existing V1 plans. It evaluates the four
predeclared context variants with separate reasons, not an arbitrary summed
score or fabricated win probability. The runtime does not yet use this layer.
It is a pure decision component, exercised by the offline comparison.

`long_short_trade_plan.py` builds entry geometry from a fresh verified USD-M
book at an explicitly supplied notional. LONG consumes asks, SHORT bids. Book
imbalance is not a direction vote. Entry impact is already in VWAP and is not
charged twice. Fees, exit slippage reserve and paid-funding scenario are explicit
and hashed. Missing cost/size, insufficient depth, excessive spread, expired
entry, wrong-side stop, chase, a known nearby obstacle, or inadequate net R/R
reject the plan. No stop widening to improve win rate. A 2R plan target is not a
forecast. This new geometry component is NOT the exit rule in the V1 historical
comparison: V1's original trailing exit remains unchanged in that comparison.

`research/trader_v2/compare.py` joins every existing August/September candidate
to official historical metrics/funding with a declared availability delay. The
delay is a reconstruction assumption; actual publication/revision history is
not verified. Native REST's latest mark/funding estimate is not silently
substituted for an already settled funding record.

`prepare_data.py` fixed 3,176 archives for 1,240 episodes before the join.
The downloader first verified 324 files with 12 workers. It was interrupted
and resumed with 32 workers against the same manifest; every resumed local file
is hash-checked. Access-policy errors abort. No alternate endpoint is attempted.
An initial process-signal attempt could not address the process namespace;
the tool session was stopped successfully with exit 130 before resumption.

Known limits still matter: historical crypto classification, settlement mark
prices and actual funding P&L, September minute exits, equal-plan controls,
portfolio/execution constraints and an unchanged forward period. V1's SHORT
results were poor; this experiment must not hide them or claim they are fixed.
Requiring previous 4h strength may miss early reversals; STRUCTURE is an
experiment, not automatically the production design. Squeeze/reversal entries
are not covered by this continuation filter.

The official primary literature supports researching momentum, participation,
costs and crowding, not a claimed 60–100% hit rate. Additional source read after
preregistration: BIS Working Paper 1087, *Crypto carry*, studies futures basis
and crash risk in BTC/ETH; it does not validate a specific Binance funding or
account-ratio threshold for hourly altcoin signals.
https://www.bis.org/publications/working-paper-1087-crypto-carry

The separate modules presently keep `actionable=false`. Their output is
research evidence, not a certification to use real capital.
