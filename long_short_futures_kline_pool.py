"""Full pattern-universe candle feed, split across bounded Binance connections.

Each shard owns a separate WebSocket and SQLite connection. Symbols keep their
shard across ranking/rotation changes. Historical repair remains rate limited
and independent; subscription coverage is never described as history readiness.
"""
import threading
import time

from long_short_futures_kline_shadow import (
    CHART_MAX_SYMBOLS, RETENTION_SECONDS, FuturesKlineStream, SOURCE, connect, log,
)

VERSION = 'FULL_PATTERN_UNIVERSE_V1_1_2026_10_23'


class FuturesKlinePool:
    def __init__(self, db, capacity=CHART_MAX_SYMBOLS, stream_factory=FuturesKlineStream,
                 retention_seconds=RETENTION_SECONDS, clock=time.monotonic):
        if not 1 <= capacity <= CHART_MAX_SYMBOLS:
            raise ValueError('INVALID_KLINE_SHARD_CAPACITY')
        self.db = db
        self.capacity = capacity
        self.stream_factory = stream_factory
        self.retention_seconds = retention_seconds
        self.clock = clock
        self.last_requested = {}
        self.lock = threading.Lock()
        self.assignments = []
        self.streams = []
        self.threads = []
        self.requested = []
        self.catalogue_current = False

    def assign(self, requested):
        requested = list(dict.fromkeys(s.upper() for s in requested
                         if isinstance(s, str) and s.upper().endswith('USDT')))
        wanted = set(requested)
        with self.lock:
            now = self.clock()
            for symbol in requested:
                self.last_requested[symbol] = now
            self.last_requested = {s: t for s, t in self.last_requested.items()
                if s in wanted or (self.retention_seconds > 0 and now-t < self.retention_seconds)}
            # A changing rank must not evict a healthy subscription.
            # The volume catalogue may briefly omit a symbol when its ticker
            # ages out. Keep its candle feed for the existing 24h retention,
            # without admitting it to scans until eligibility is fresh again.
            groups = [[s for s in group if s in self.last_requested] for group in self.assignments]
            assigned = {s for group in groups for s in group}
            for symbol in requested:
                if symbol in assigned:
                    continue
                group = next((g for g in groups if len(g) < self.capacity), None)
                if group is None:
                    group = []
                    groups.append(group)
                group.append(symbol)
                assigned.add(symbol)
            self.assignments = groups
            self.requested = requested
            self.catalogue_current = True
            return [list(g) for g in groups]

    def shard_request(self, index):
        with self.lock:
            return list(self.assignments[index])

    def _worker(self, index, stream, stop):
        while not stop.is_set():
            try:
                stream.run(lambda: self.shard_request(index), stop, refresh_seconds=15)
            except Exception as exc:
                log('FUTURES_KLINE_SHARD_ERROR', dict(shard=index, error_type=type(exc).__name__))
            if not stop.is_set():
                stop.wait(5)

    def start_shards(self, stop):
        while len(self.streams) < len(self.assignments):
            index = len(self.streams)
            stream = self.stream_factory(self.db, max_symbols=self.capacity,
                                         retention_seconds=0, shard_id=index)
            worker = threading.Thread(target=self._worker, args=(index, stream, stop),
                                      name=f'futures-kline-shard-{index}', daemon=True)
            self.streams.append(stream)
            self.threads.append(worker)
            worker.start()

    def coverage(self, now_ms=None):
        now_ms = int(time.time()*1000) if now_ms is None else now_ms
        with self.lock:
            requested = set(self.requested)
            groups = [list(g) for g in self.assignments]
        subscribed = set()
        shards = []
        for index, stream in enumerate(self.streams):
            connected = stream.connected_at is not None
            symbols = set(stream.subscribed) if connected else set()
            subscribed.update(symbols)
            shards.append(dict(shard=index, assigned=len(groups[index]),
                subscribed=len(symbols), connected=connected,
                streams=len(symbols)*len(stream.intervals),
                rows_written=stream.totals['rows_written'],
                pending_rows=len(stream.buffer), disconnects=stream.totals['disconnects']))
        fresh = set()
        quote_error = None
        try:
            c = connect(self.db, timeout=3)
            try:
                fresh = {r[0] for r in c.execute('''SELECT symbol FROM futures_last_price
                    WHERE source=? AND event_ms BETWEEN ? AND ?
                    AND received_ms BETWEEN ? AND ? AND price>0''',
                    (SOURCE, now_ms-30_000, now_ms, now_ms-30_000, now_ms))}
            finally:
                c.close()
        except Exception as exc:
            quote_error = type(exc).__name__
        missing = requested-subscribed
        stale = requested-fresh
        return dict(version=VERSION, observed_ms=now_ms,
            catalogue_current=self.catalogue_current, requested=len(requested),
            assigned=sum(len(g) for g in groups), subscribed=len(requested & subscribed),
            retained_symbols=sum(len(g) for g in groups)-len(requested),
            uncovered_count=len(missing), uncovered_sample=sorted(missing)[:20],
            fresh_price_symbols=len(requested & fresh), stale_price_count=len(stale),
            stale_price_sample=sorted(stale)[:20], quote_read_error=quote_error,
            shards=shards, history_readiness='SEE_PATTERN_HEALTH')

    def run(self, requested_fn, stop, refresh_seconds=30):
        try:
            while not stop.is_set():
                try:
                    self.assign(requested_fn())
                    self.start_shards(stop)
                except Exception as exc:
                    self.catalogue_current = False
                    log('FUTURES_KLINE_UNIVERSE_UNAVAILABLE', dict(error_type=type(exc).__name__))
                log('FUTURES_KLINE_POOL_HEALTH', self.coverage())
                stop.wait(refresh_seconds)
        finally:
            stop.set()
            deadline = time.monotonic()+15
            for worker in self.threads:
                worker.join(timeout=max(0, deadline-time.monotonic()))
