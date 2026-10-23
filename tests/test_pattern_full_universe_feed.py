import io
import json
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import long_short_futures_kline_shadow as feed
import long_short_futures_history_worker as history
import long_short_pattern_runtime as runtime
import long_short_patterns as core
from long_short_futures_kline_pool import FuturesKlinePool
from tests.test_pattern_engine import triangle, context, candle, START, M, H
from tests.test_long_short_futures_kline_shadow import packet


class FullUniverseFeedTests(unittest.TestCase):
    def test_demand_and_history_requests_include_every_eligible_coin_and_active_plan(self):
        universe = [(f'C{i:03d}USDT', 3e6, 10, 0) for i in range(749)]
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(runtime, 'root', return_value=Path(directory)), \
             patch.object(runtime, 'universe', return_value=universe), \
             patch.dict('os.environ', {'LS_RUNTIME_PROFILE': 'PATTERN_STRUCTURE_V2'}):
            c = runtime.connect()
            c.execute('INSERT INTO plans VALUES(?,?,?,?,?,?,?,?,NULL,NULL)',
                      ('old', 'ACTIVEUSDT', 'LONG', 1, 1, 1, 'TRACKING', '{}'))
            c.commit(); c.close()
            full, urgent = runtime.demand(166, full_chart=True)
            self.assertEqual(len(full), 751)
            self.assertEqual(full[:2], ['ACTIVEUSDT', 'BTCUSDT'])
            self.assertEqual(urgent, ['ACTIVEUSDT'])
            self.assertEqual(set(feed.requested_symbols('unused')), set(full))
            self.assertEqual(len(runtime.demand(90)[0]), 90)

    def test_all_symbols_fit_once_without_churn_when_ranking_changes(self):
        pool = FuturesKlinePool('unused')
        symbols = [f'C{i:03d}USDT' for i in range(749)]
        first = pool.assign(symbols)
        self.assertEqual(len(first), 5)
        self.assertEqual([s for g in first for s in g], symbols)
        self.assertTrue(all(len(g)*len(feed.INTERVALS) <= 1000 for g in first))
        self.assertEqual(pool.assign(list(reversed(symbols))), first)
        changed = pool.assign(symbols[20:]+['NEWUSDT'])
        self.assertEqual({s for g in changed for s in g}, set(symbols[20:]+['NEWUSDT']))
        self.assertEqual(sum(map(len, changed)), 730)
        for index, group in enumerate(first):
            self.assertTrue(set(group[20:] if index == 0 else group).issubset(changed[index]))

    def test_coverage_distinguishes_subscription_from_actual_fresh_prices(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory)/'candles.db')
            c = feed.initialize(path)
            c.executemany('INSERT INTO futures_last_price VALUES(?,?,?,?,?)',
                [('AUSDT', 10, 100_000, 100_000, feed.SOURCE),
                 ('BUSDT', 10, 1, 100_000, feed.SOURCE),
                 ('CUSDT', 10, 100_000, 100_000, 'UNVERIFIED')])
            c.commit(); c.close()
            pool = FuturesKlinePool(path)
            pool.assign(['AUSDT', 'BUSDT', 'CUSDT'])
            stream = SimpleNamespace(connected_at=1, subscribed=['AUSDT', 'BUSDT', 'CUSDT'],
                intervals=feed.INTERVALS, totals={'rows_written': 0, 'disconnects': 0}, buffer=[])
            pool.streams = [stream]
            status = pool.coverage(100_000)
            self.assertEqual(status['subscribed'], 3)
            self.assertEqual(status['fresh_price_symbols'], 1)
            self.assertEqual(status['stale_price_count'], 2)
            stream.connected_at = None
            self.assertEqual(pool.coverage(100_000)['uncovered_count'], 3)

    def test_two_real_stream_loops_persist_both_shards_and_stop_cleanly(self):
        stop = threading.Event()
        barrier = threading.Barrier(2)
        modules = []

        class Module:
            class WebSocketTimeoutException(Exception):
                pass

            def __init__(self, symbol):
                self.symbol, self.sent, self.closed, self.calls = symbol, [], False, 0

            def create_connection(self, *args, **kwargs):
                return self

            def settimeout(self, value):
                pass

            def send(self, text):
                self.sent.append(json.loads(text))

            def recv(self):
                self.calls += 1
                if self.calls == 1:
                    return json.dumps(packet(self.symbol, '1m', M))
                barrier.wait(timeout=5)
                stop.set()
                raise self.WebSocketTimeoutException()

            def close(self):
                self.closed = True

        def factory(db, **kwargs):
            module = Module(['AUSDT', 'BUSDT'][kwargs['shard_id']])
            modules.append(module)
            return feed.FuturesKlineStream(db, ws_module=module, sleep=lambda _: None, **kwargs)

        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            path = str(Path(directory)/'candles.db')
            feed.initialize(path).close()
            pool = FuturesKlinePool(path, capacity=1, stream_factory=factory)
            pool.run(lambda: ['AUSDT', 'BUSDT'], stop, refresh_seconds=.02)
            c = feed.connect(path)
            try:
                self.assertEqual({r[0] for r in c.execute('SELECT symbol FROM futures_closed_klines')},
                                 {'AUSDT', 'BUSDT'})
            finally:
                c.close()
            self.assertTrue(all(not t.is_alive() for t in pool.threads))
            self.assertTrue(all(m.closed for m in modules))
            self.assertEqual([len(m.sent[0]['params']) for m in modules], [6, 6])


class PatternRepairAndDiagnosticsTests(unittest.TestCase):
    def test_repair_uses_entire_pattern_window_and_its_actual_context_minimum(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict('os.environ', {'LS_RUNTIME_PROFILE': 'PATTERN_STRUCTURE_V2'}):
            path = str(Path(directory)/'candles.db')
            c = feed.initialize(path)
            step = 15*M
            now = START+221*step
            rows = [candle(START+i*step, step=step) for i in range(221) if i != 30]
            def add(iv, bars):
                feed.upsert_rows(c, [('TESTUSDT', iv, b['open_ms'], b['close_ms'],
                    b['open'], b['high'], b['low'], b['close'], 1, feed.REST_SOURCE,
                    b['close_ms']+1, 100, 1, 50) for b in bars])
                c.commit()
            add('15m', rows)
            add('4h', context(now))
            from long_short_data_health import interval_status
            self.assertTrue(interval_status(c, 'TESTUSDT', '15m', now+1000)['ready'])
            self.assertTrue(history.needs_repair(path, 'TESTUSDT', '15m', now+1000))
            self.assertFalse(history.needs_repair(path, 'TESTUSDT', '4h', now+1000))
            add('15m', [candle(START+30*step, step=step)])
            self.assertFalse(history.needs_repair(path, 'TESTUSDT', '15m', now+1000))
            self.assertTrue(history.needs_repair(path, 'TESTUSDT', '15m', now+step+1000))
            c.close()

    def test_diagnostics_do_not_change_plans_and_explain_all_current_shapes(self):
        rows = triangle(); now = rows[-1]['close_ms']+1
        diagnostics = {}
        original = core.assess('TESTUSDT', '30m', rows, context(now), now)
        observed = core.assess('TESTUSDT', '30m', rows, context(now), now, diagnostics)
        self.assertEqual(observed, original)
        self.assertEqual(diagnostics['assessed_pairs'], 1)
        self.assertEqual(diagnostics['shapes_considered'], sum(diagnostics.get(k, 0) for k in
                         ('shape_already_broken', 'shape_awaiting_break', 'fresh_breaks')))
        self.assertEqual(len(observed), diagnostics.get('plans_ready', 0)+diagnostics.get('plans_rejected', 0))

    def test_repeated_scans_keep_readiness_and_funnel_without_double_counting(self):
        rows = triangle(); now = rows[-1]['close_ms']+1
        def read(db, symbol, interval, timestamp):
            if symbol == 'BADUSDT':
                return []
            return context(now) if interval == '4h' else rows
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(runtime, 'universe', return_value=[('GOODUSDT',3e6,110,0),('BADUSDT',3e6,1,0)]), \
             patch.object(runtime, 'read_bars', side_effect=read), \
             patch.object(runtime, 'SETUP_INTERVALS', ('30m',)), redirect_stdout(io.StringIO()):
            c = runtime.connect(Path(directory)/'plans.db')
            try:
                first = runtime.scan(c, None, now)
                second = runtime.scan(c, None, now+1000)
                self.assertEqual(first['current_bar_assessments'], second['current_bar_assessments'])
                self.assertEqual(first['data_ready_symbol_intervals'], 1)
                self.assertEqual(second['data_ready_symbol_intervals'], 1)
                self.assertEqual(second['newly_scanned'], 0)
                self.assertEqual(second['already_scanned'], 1)
                self.assertEqual(second['new_assessments'], {})
                self.assertEqual(second['unique_candidates_added'], {})
                self.assertEqual(second['failed_symbol_intervals'], {'INSUFFICIENT_CLOSED_HISTORY': 1})
                self.assertEqual(second['eligible_symbol_intervals'],
                    second['data_ready_symbol_intervals']+sum(second['failed_symbol_intervals'].values()))
                self.assertEqual(c.execute('SELECT COUNT(*) FROM plans').fetchone()[0], 0)
            finally:
                c.close()


if __name__ == '__main__':
    unittest.main()
