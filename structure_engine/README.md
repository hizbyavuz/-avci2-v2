# Structure Context V1

This independent research implementation uses 4h structure, 1h context and a
15m setup. 30m is an alternative cohort, never an extra compulsory gate.
It does not modify or automatically start any previous motor.

## What is implemented

- Causal significant swing anchors and zones, with known-at timestamps.
- Near-level WATCH, fresh closed break, frozen-zone retest, and LATE classification.
- Double top/bottom descriptive tags when the same decision structure fits.
- Higher timeframe direction as an explicit warning/context, not unanimity.
- First meaningful opposing zone, structural stop, cost-adjusted risk/room.
- Fibonacci A/B selected from the same significant leg; annotation only.
- Exact decision-coordinate PNG renderer (4h, 1h, setup), no future candles.
- Separate immutable snapshots, append-only scan/event/outcome/health evidence.
- Default local-only operation. Explicit optional chart+text Telegram receipts,
  no blind retry after uncertain delivery; invalidated/expired idea updates.
- Research reference after observation; delivered reference after BOTH receipts.
- 1m outcome paths, conservative ambiguous-bar ordering, gap stops, explicit
  missing data. No actual fills, order books, funding or profit guarantees.
- Bounded reads, one scan per closed setup candle, state/disk/RSS guards, supervisor
  that stops all children if a required worker fails or the resource guard trips.

## Inspect existing recorded data, with no network publication

```bash
python -m unittest -v tests.test_structure_context tests.test_pattern_engine
python -m structure_engine.runtime \
  --source-db /existing/state/binance_futures_kline_shadow.db \
  --state-dir /separate/state/structure_context_v1 --once
```

The source is SQLite read-only. With `--symbols BTCUSDT,ETHUSDT`, inspect a fixed
subset. Without that option, use the existing verified Binance coin universe.
Default interval is 15m; use `--interval 30m` in a separate state directory for
the alternative. Do not run two notifiers against the same state directory.

## Reproduce the fixed historical review

```bash
python -m structure_engine.replay --cache /path/to/archive-cache \
  --out research/structure_context_v1/reproduction
```

Use `--offline` after the 12 SHA256-verified monthly archives are cached. The
protocol, period/symbol choices and limitations are in
`research/structure_context_v1/PROTOCOL.md`. Replays never contact Telegram.

## Live activation is NOT part of this delivery

`Dockerfile.structure` is a prepared optional entry point. `LS_RUNTIME_PROFILE`
must explicitly equal `STRUCTURE_CONTEXT_V1`. `STRUCTURE_SETUP_INTERVAL` defaults
to 15m. All existing Railway services keep their current stopped command.
The supervisor reuses shared full-universe collectors with publication disabled
in those children; it starts only `structure_engine.runtime` as decision owner.

Notifications additionally require `STRUCTURE_NOTIFICATIONS=1` and the existing
explicit `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`. No new chat is inferred. Default
is local-only. Source/feed freshness still gates decisions: stale data is not
reported as zero opportunity. Missing or partial Telegram receipts are unresolved.

## Boundaries

No automatic exchange orders. No claim that a drawn setup works merely because
the image is consistent. No IFVG/FVG/order-block detector in this release: the
three user screenshots were examples, not permission to add unvalidated labels.
The implemented first scope is meaningful level approach/break/retest and its
double-top/bottom subset; sweep/reclaim or other families need separate rules.

Resource guards are process protections, not Railway billing limits. Collector
and volume costs, monthly credits and hard spending limits must be checked before
activation. Existing volumes/backups have NOT been removed. Saved outcomes and
raw evidence are not deleted automatically to stay below a budget.

If code/config changes, use a new version and a new state database. Existing
manifests reject changed code under the same version. Keep winners AND failures.
