"""Offline tests for the Binance USD-M closed-candle recorder (no network)."""
import io
import json
import os
import sqlite3
import tempfile
import threading
import unittest
import zipfile
import datetime as dt
from contextlib import redirect_stdout

import long_short_futures_kline_shadow as k
from long_short_futures_kline_shadow import initialize, parse_closed_kline, store

M = 60000


def packet(symbol, interval, open_ms, close=11.0, closed=True, **extra):
    period = k.PERIOD_MS[interval]
    bar = {"i": interval, "t": open_ms, "T": open_ms + period - 1,
           "o": "10", "h": "12", "l": "9", "c": str(close), "v": "20",
           "q": "200", "n": 7, "Q": "90", "x": closed}
    bar.update(extra)
    return {"stream": symbol.lower() + "@kline_" + interval,
            "data": {"e": "kline", "s": symbol, "k": bar}}


class ParseAndStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "k.db")

    def test_only_closed_futures_candles(self):
        p = packet("BTCUSDT", "5m", 300000, closed=False)
        self.assertIsNone(parse_closed_kline(p))
        p["data"]["k"]["x"] = True
        row = parse_closed_kline(p)
        self.assertEqual(row[:4], ("BTCUSDT", "5m", 300000, 599999))
        self.assertEqual(row[7], 11.0)
        self.assertEqual(row[9], k.SOURCE)

    def test_missing_fields_rejected_without_exception(self):
        p = packet("BTCUSDT", "1m", M)
        del p["data"]["k"]["q"]
        self.assertIsNone(parse_closed_kline(p))
        self.assertIsNone(parse_closed_kline({"result": None, "id": 1}))
        self.assertIsNone(parse_closed_kline({"data": "garbage"}))

    def test_upsert_is_idempotent(self):
        con = initialize(self.db)
        row = parse_closed_kline(packet("ETHUSDT", "1m", M))
        store(con, row)
        stats = store(con, row)  # previously raised IntegrityError and killed the feed
        self.assertEqual(stats["duplicate"], 1)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM futures_closed_klines").fetchone()[0], 1)
        con.close()

    def test_wal_and_busy_timeout(self):
        con = initialize(self.db)
        self.assertEqual(con.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertGreaterEqual(con.execute("PRAGMA busy_timeout").fetchone()[0], 30000)
        con.close()

    def _legacy_null_row(self, con, close=11.0):
        con.execute("""INSERT INTO futures_closed_klines(symbol,interval,open_ms,close_ms,
            open,high,low,close,volume,source,received_ms) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    ("UNIUSDT", "5m", 300000, 599999, 10, 12, 9, close, 20, k.SOURCE, 1))
        con.commit()

    def test_archive_repairs_legacy_websocket_row_with_null_fields(self):
        con = initialize(self.db)
        self._legacy_null_row(con)
        archive = ("UNIUSDT", "5m", 300000, 599999, 10.0, 12.0, 9.0, 11.0, 20.0,
                   k.ARCHIVE_SOURCE, 2, 200.0, 7, 90.0)
        stats = store(con, archive)
        self.assertEqual(stats["repaired"], 1)
        row = con.execute("SELECT source,quote_volume,trade_count,taker_buy_quote FROM futures_closed_klines").fetchone()
        # OHLCV identical: fields filled, original provenance kept.
        self.assertEqual(row, (k.SOURCE, 200.0, 7, 90.0))
        con.close()

    def test_incomplete_row_with_different_ohlc_is_replaced_and_logged(self):
        con = initialize(self.db)
        self._legacy_null_row(con, close=11.5)
        archive = ("UNIUSDT", "5m", 300000, 599999, 10.0, 12.0, 9.0, 11.0, 20.0,
                   k.ARCHIVE_SOURCE, 2, 200.0, 7, 90.0)
        out = io.StringIO()
        with redirect_stdout(out):
            stats = store(con, archive)
        self.assertEqual((stats["repaired"], stats["conflict"]), (1, 1))
        self.assertIn("FUTURES_KLINE_CONFLICT", out.getvalue())
        self.assertEqual(con.execute("SELECT source,close FROM futures_closed_klines").fetchone(),
                         (k.ARCHIVE_SOURCE, 11.0))
        con.close()

    def test_complete_conflicting_row_is_kept_and_logged(self):
        con = initialize(self.db)
        store(con, parse_closed_kline(packet("UNIUSDT", "5m", 300000)))
        other = ("UNIUSDT", "5m", 300000, 599999, 10.0, 12.0, 9.0, 11.7, 20.0,
                 k.ARCHIVE_SOURCE, 2, 200.0, 7, 90.0)
        with redirect_stdout(io.StringIO()):
            stats = store(con, other)
        self.assertEqual(stats["conflict"], 1)
        self.assertEqual(con.execute("SELECT close FROM futures_closed_klines").fetchone()[0], 11.0)
        con.close()

    def test_gap_tracker_reports_first_missing_bar_and_count(self):
        con = initialize(self.db)
        store(con, parse_closed_kline(packet("BTCUSDT", "5m", 300000)))
        tracker = k.GapTracker()
        gap = tracker.observe(con, parse_closed_kline(packet("BTCUSDT", "5m", 300000 * 5)))
        self.assertEqual(gap["first_missing_open_ms"], 600000)
        self.assertEqual(gap["missing_bars"], 3)
        self.assertIsNone(tracker.observe(con, parse_closed_kline(packet("BTCUSDT", "5m", 300000 * 6))))
        con.close()


class FakeWS:
    """Scripted WebSocket. Script items: str frame, Exception instance, or callable."""

    def __init__(self, owner, script):
        self.owner = owner
        self.script = list(script)
        self.sent = []
        self.closed = False

    def settimeout(self, _):
        pass

    def send(self, data):
        self.sent.append(json.loads(data))

    def recv(self):
        if not self.script:
            self.owner.stop.set()
            raise self.owner.WebSocketTimeoutException()
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return item()
        return item

    def close(self):
        self.closed = True


class FakeWSModule:
    class WebSocketTimeoutException(Exception):
        pass

    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.connections = []
        self.stop = threading.Event()

    def create_connection(self, url, timeout=None, enable_multithread=None):
        ws = FakeWS(self, self.scripts.pop(0) if self.scripts else [])
        self.connections.append(ws)
        return ws


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        self.t += 0.5
        return self.t


class StreamTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "k.db")

    def stream(self, mod, **kw):
        return k.FuturesKlineStream(self.db, ws_module=mod, clock=Clock(), sleep=lambda _: None, **kw)

    def rows(self):
        with sqlite3.connect(self.db) as con:
            return con.execute("SELECT symbol,interval,open_ms FROM futures_closed_klines ORDER BY symbol,open_ms").fetchall()

    def test_duplicates_and_bad_frames_do_not_drop_connection(self):
        p = json.dumps(packet("BTCUSDT", "1m", M))
        mod = FakeWSModule([[json.dumps({"result": None, "id": 101}), p, p, "not json",
                             json.dumps(packet("XRPUSDT", "1m", M))]])  # XRP not subscribed
        s = self.stream(mod)
        with redirect_stdout(io.StringIO()):
            s.run(lambda: ["BTCUSDT"], mod.stop, refresh_seconds=10**9)
        self.assertEqual(len(mod.connections), 1)
        self.assertEqual(s.totals["disconnects"], 0)
        self.assertEqual(self.rows(), [("BTCUSDT", "1m", M)])
        self.assertEqual(s.totals["duplicates"], 1)
        self.assertEqual(s.totals["invalid_messages"], 1)
        params = [x for m in mod.connections[0].sent for x in m["params"]]
        self.assertEqual(len(params), 6)  # 1 symbol x 6 intervals

    def test_symbol_changes_use_subscribe_without_reconnecting(self):
        requested = [["BTCUSDT"]]
        mod = FakeWSModule([[json.dumps(packet("BTCUSDT", "1m", M)),
                             lambda: (requested.append(["BTCUSDT", "ETHUSDT"]) or
                                      json.dumps(packet("BTCUSDT", "1m", 2 * M))),
                             json.dumps(packet("ETHUSDT", "1m", 2 * M))]])
        s = self.stream(mod)
        with redirect_stdout(io.StringIO()):
            s.run(lambda: requested[-1], mod.stop, refresh_seconds=0.1)
        self.assertEqual(len(mod.connections), 1)
        methods = [m["method"] for m in mod.connections[0].sent]
        self.assertIn("SUBSCRIBE", methods)
        self.assertNotIn("UNSUBSCRIBE", methods)
        self.assertIn(("ETHUSDT", "1m", 2 * M), self.rows())

    def test_disconnect_reconnects_and_logs_gap(self):
        mod = FakeWSModule([[json.dumps(packet("BTCUSDT", "1m", M)), ConnectionError("reset")],
                            [json.dumps(packet("BTCUSDT", "1m", 4 * M))]])
        s = self.stream(mod)
        out = io.StringIO()
        with redirect_stdout(out):
            s.run(lambda: ["BTCUSDT"], mod.stop, refresh_seconds=10**9)
        self.assertEqual(len(mod.connections), 2)
        self.assertEqual(s.totals["disconnects"], 1)
        self.assertEqual(s.totals["missing_bars_detected"], 2)
        self.assertIn('"first_missing_open_ms": 120000', out.getvalue())
        self.assertEqual(len(self.rows()), 2)  # gap is NOT filled with invented candles

    def test_write_lock_keeps_rows_buffered_until_lock_released(self):
        initialize(self.db).close()
        blocker = sqlite3.connect(self.db, timeout=1)
        blocker.execute("BEGIN IMMEDIATE")
        s = self.stream(FakeWSModule([]))
        s.buffer.append(parse_closed_kline(packet("BTCUSDT", "1m", M)))
        con = k.connect(self.db, timeout=0.2)
        with redirect_stdout(io.StringIO()):
            self.assertFalse(s.flush(con))
        self.assertEqual(len(s.buffer), 1)
        blocker.rollback(); blocker.close()
        self.assertTrue(s.flush(con))
        self.assertEqual(self.rows(), [("BTCUSDT", "1m", M)])
        con.close()

    def test_capacity_reports_uncovered_symbols(self):
        s = self.stream(FakeWSModule([]), max_symbols=2)
        target, uncovered = s.plan(["AUSDT", "BUSDT", "CUSDT"])
        self.assertEqual(target, ["AUSDT", "BUSDT"])
        self.assertEqual(uncovered, ["CUSDT"])

    def test_recently_requested_symbol_is_retained(self):
        s = self.stream(FakeWSModule([]))
        s.subscribed, _ = s.plan(["AUSDT", "BUSDT"])
        target, _ = s.plan(["AUSDT"])
        self.assertEqual(target, ["AUSDT", "BUSDT"])

    def test_stream_budget_guard(self):
        with self.assertRaises(ValueError):
            k.FuturesKlineStream(self.db, max_symbols=200)

    def test_data_survives_restart(self):
        con = initialize(self.db)
        store(con, parse_closed_kline(packet("BTCUSDT", "1m", M)))
        con.close()
        con = initialize(self.db)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM futures_closed_klines").fetchone()[0], 1)
        con.close()


def make_zip(symbol, interval, rows):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        lines = ["open_time,open,high,low,close,volume,close_time,quote_volume,count,"
                 "taker_buy_volume,taker_buy_quote_volume,ignore"]
        for open_ms in rows:
            close_ms = open_ms + k.PERIOD_MS[interval] - 1
            lines.append(f"{open_ms},10,12,9,11,20,{close_ms},200,7,9,90,0")
        z.writestr(f"{symbol}-{interval}.csv", "\n".join(lines))
    return buf.getvalue()


class ArchiveTests(unittest.TestCase):
    def test_archive_import_and_miss_classification(self):
        import urllib.error
        today = dt.date(2026, 10, 13)

        def fetch(url):
            if url.endswith("2026-10-12.zip"):
                raise urllib.error.HTTPError(url, 404, "nf", None, None)
            base = int(dt.datetime(2026, 10, 11, tzinfo=dt.timezone.utc).timestamp() * 1000)
            return make_zip("BTCUSDT", "5m", [base, base + 300000])

        with tempfile.TemporaryDirectory() as folder:
            db = os.path.join(folder, "k.db")
            with redirect_stdout(io.StringIO()):
                out = k.backfill_binance_archive(db, ["BTCUSDT"], intervals=("5m",), fetch=fetch, today=today)
            self.assertEqual(out["recent_misses"], 1)
            self.assertEqual(out["miss_reasons"], {"not_published_or_not_listed": 1})
            with sqlite3.connect(db) as con:
                self.assertEqual(con.execute("SELECT COUNT(DISTINCT open_ms), MIN(source) FROM futures_closed_klines").fetchone(),
                                 (2, k.ARCHIVE_SOURCE))

    def test_archive_never_requests_today(self):
        urls = k.archive_urls("BTCUSDT", "4h", dt.date(2026, 10, 13))
        self.assertTrue(all("2026-10-13" not in u for _, _, u in urls))
        self.assertIn("monthly", {kind for kind, _, _ in urls})


if __name__ == "__main__":
    unittest.main()
