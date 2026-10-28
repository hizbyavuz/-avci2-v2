# Binance outage recovery, 29 October 2026

## Verified production state

The existing Railway `long-short-motor` service runs commit
`a00ae6bc2bd986dcc712bdffe0979452bbbee571` in deployment
`9fcd5967-cb69-4dd7-a00c-314cd73f1787` (SUCCESS). At
2026-10-28 22:05 UTC its ten sockets were healthy, covering 518 symbols,
with 108,838 closed bars received. A 309/309 scan completed at 20:01 UTC;
Telegram acknowledged Binance and separate Bybit WATCH photos after 21:00 UTC.
These facts establish data and delivery connectivity, not profitable trading.

Binance returned HTTP 418 on `/fapi/v1/exchangeInfo` at 20:30 UTC. The persisted
exchange boundary is `1793303401674`, or **29 October 2026 22:50:01.674
Europe/Istanbul**. The response did not identify the underlying cause. A shared
egress address remains only a hypothesis; this change does not claim to remove
the ban, rotate IPs or retry before that boundary.

## Repair

- Retry missing candle history once a minute when the existing market stream
  is ready. Previously a REST ban deferred local cache checks to the end of
  that ban, even if the archive or stream had already repaired the gap. Every
  actual REST request still uses the unchanged durable gate; entry admission
  still requires fresh venue metadata and order-book validation.
- Record scan progress only when its evidence changes. Repeated idle polls
  neither append duplicate progress events nor rewrite the same checkpoint.
  Complete/expired scans still retain their terminal evidence.
- Use bounded idle polling while preserving quick active batches and waking
  at a short admission cooldown boundary.
- Keep one waiting notification throughout a continuous exchange-access ban,
  including when a cached scan completes or expires and across restarts.
  Recovery is reported when access returns. Candidate delivery is unchanged.
- Encode Unicode archive paths on the wire while preserving original cache
  keys, checksums and date validation. The previously failing Chinese-named
  symbols are covered by regression tests; live archive availability is not
  asserted.

## Validation and release limits

All **179 TA Paper tests pass** with external sockets disabled; localhost
WebSocket integration tests are included. `git diff --check` passes. The
strategy configuration remains
`f4485acc0340fb6352cde17558ada6f53459bafa486793423792c3b4e2ccdedd`.
`core.py`, `rate_limit.py`, `io.py`, `watch.py`, `observations.py` and
`external_watch.py` are byte-identical to the live parent.

No live restart should be performed while the ten existing sockets are healthy
and the exchange ban is active: reconnecting would be deferred by that same
ban. Prepare the source and provenance changes for the existing service, then
verify the deployment and renewed connectivity after the exchange permits
connections. Preserve the current start command's explicit user retry floor,
the persistent volume, one replica, region, resource limits and venue separation.
The staged source is not evidence of deployment or ban recovery.

The heartbeat reported **204,902,400 free bytes** on the state filesystem at
22:05 UTC. This patch reduces unnecessary write activity but is not a disk
capacity fix. No observations, raw evidence or cached histories were deleted;
capacity must be checked before a later release.
