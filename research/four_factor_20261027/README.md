# Four fixed hypotheses: offline archive study

This is an isolated research runner based on deployed commit
`7f80c3d7189a8c4e100314a814533900b78626c0`. It does not change the production
worker, its strategy, exchange retry floor, Telegram delivery or order policy.
`protocol.json` fixes the study before outcome calculation. A passing arm is
only a research candidate; no automatic promotion is implemented.

## Run

Python 3.12, NumPy 2.3.5 and aiohttp 3.14.4 were used. These are research-only
dependencies; the production requirements are unchanged. Run on a separate
machine with sufficient scratch disk, never on the small production volume.

```sh
python -m research.four_factor_20261027.download --cache /data/cache --output /data/run
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python -u -m research.four_factor_20261027.study --cache /data/cache --output /data/run --workers 4
```

The downloader uses only Binance's static public archive and S3 catalogue.
Every ZIP is checked against its published SHA256. An access restriction or
rate-limit response stops the run; no REST endpoint or alternate IP is used.
Monthly 30m/4h candles identify candidates; only then are required 1m execution,
mark-price, funding and daily metrics archives fetched. Outcomes are saved per
symbol; reproducible 1m ZIPs are removed after that checkpoint. Other archives,
their checksums and download inventories remain available. Missing data is
reported explicitly. A completed process is not proof of complete data.

The full historical symbol catalogue is used, including delisted symbols.
`universe_classification.json` records 145 non-crypto contracts checked against
39 official launch announcements before outcomes, five additional uncertain
names explicitly excluded, index exclusions and the SPX6900 crypto exception.
This corrects a research-universe defect without changing live code. Historical
exchange metadata, instrument transitions and exact listing dates are still
not fully reconstructed; 30 days since the first available bar is an age proxy.

`--symbols` creates an explicitly labelled software/data pilot. Never present
that result as the full study. Restart with the same command to reuse completed
symbol checkpoints. A code/protocol/taxonomy mismatch rejects directory reuse;
an intervention requires a new run directory and an explicit audit note.

## Interpretation

The signal window is October 2025 through July 2026, with September warmup and
an August execution buffer. It is exploratory because prior exposure has not
been verified. The old August 2026 result is never pooled into this study.

The four filters run individually. Threshold search and stacked filters are
absent. Core strategy assessment uses the same 119 closed bars as live. A
vector pre-screen only saves computation; `core.assess` admits every setup.
Equal-weight UTC entry-day mean net R is primary; overlapping trades are not
independent observations. Fixed calendar-week block resamples provide paired
comparisons, with correction for 24 directional hypothesis/comparator tests.
Monthly and BTC-regime slices are descriptive, not additional selection tests.

The 1.5x cost scenario charges extra baseline fees, assumed spread and slippage
to exactly the same admitted trades, quantities, exit events and original risk
denominators. It does not resize or reject expensive trades. Funding is unchanged.
Historical lot/tick rules, depth and actual notification delays are assumptions.
No result is presented as an actual executed portfolio or live profitability.

The separate capacity portfolio uses a shared LONG/SHORT ledger. Censored or
unresolved positions retain their slots; later admissions may consequently be
unidentifiable. No artificial 72-hour exit is invented. Unknown outcomes are
also shown in a separate -1R sensitivity, which is not a guaranteed worst case.

Results: `summary.json`, detailed `report.json`, `run_identity.json`,
`universe.json`, `status.json`, download inventories, and compressed per-symbol
`candidates/` and `outcomes/`. Keep the full set for audit, not only winning arms.

## Verification

```sh
PYTHONPATH=tests:. python -m unittest discover -s tests -p 'test_ta_paper*.py'
PYTHONPATH=tests:. python -m unittest discover -s tests -p 'test_four_factor_study.py'
```

Tests cover causal windows, EMA parity, matched controls, gaps, checksums,
missing features, immutable live core, equal-day statistics and same-event cost
stress. Synthetic tests establish software behavior, not a profitable edge.
