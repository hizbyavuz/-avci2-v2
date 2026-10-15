# Episode path and message identity correction

Authorized 2026-10-15 19:52 Turkey time after code/live audit. Prior live commit:
0def73958846ec7e60ab6d70d5505d7ba7061226.

Reproduced defect: a LONG with stop 100.374298 had a closed-candle low 100.174298,
then recovered to 100.64. The old advance path returned READY and attempted a
notification because it read only last price and 5m closes.

V3.1 now checks the complete 1m path from episode creation, including a separate
latest forming 1m OHLC snapshot. That snapshot is written by the existing native
WebSocket collector, one row per symbol, and never used as a closed entry candle.
Known stop or obstacle touches terminate the episode even after price recovery.
The initial partial minute, both levels within one minute, or a gap before a
touch are explicitly ambiguous: expire without claiming a known winning/losing
ordering. Missing, stale or invalid paths cannot authorize a new READY. This is
bounded by source update latency; it is not tick-perfect automated execution.
Terminal states stay terminal even if their Telegram completion edit fails.

Moved message rendering to long_short_chart_messages.py, excluded from decision
identity. Actual strategy/lifecycle code still changes strategy identity and
separates cohorts. This corrective release intentionally closes old-version
episodes once, with an explicit rule-change explanation; later edits confined
to the renderer do not reset episodes or the performance cohort.

No direction thresholds, setup models, cost assumptions or horizon changes.
Old records remain. The resolver recognizes V2, V3 and V3.1 outcome semantics.
New regression cases cover mirrored recovered stop touches, forming/closed bars,
target ordering, gaps/staleness/provenance, initial ambiguity, notification retry,
restart, and renderer-versus-decision identity separation.
