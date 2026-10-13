"""Read-only data health report: coverage, reasons, ETA, venue-separated outcomes."""
import json
import os
import sqlite3
import tempfile
import time
import unittest

import long_short_data_health as h
import long_short_futures_kline_shadow as k


def bars(symbol, interval, n, now_ms, skip=(), fields=True):
    p = k.PERIOD_MS[interval]
    last = (now_ms // p) * p - p
    return [(symbol, interval, last - i * p, last - i * p + p - 1, 10.0, 12.0, 9.0, 11.0, 20.0,
             k.SOURCE, now_ms, 200.0 if fields else None, 7 if fields else None, 90.0 if fields else None)
            for i in range(n) if i not in skip]


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sd = self.tmp.name
        self.now = int(time.time() * 1000)
        con = k.initialize(os.path.join(self.sd, "binance_futures_kline_shadow.db"))
        rows = []
        for iv in k.INTERVALS:
            rows += bars("BTCUSDT", iv, 230, self.now)
        rows += bars("ETHUSDT", "5m", 230, self.now, skip=(4,))
        k.upsert_rows(con, rows)
        con.executemany("INSERT INTO futures_closed_klines VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        bars("UNIUSDT", "15m", 130, self.now, fields=False))
        con.commit(); con.close()
        with sqlite3.connect(os.path.join(self.sd, "long_short_analyst.db")) as a:
            a.execute("""CREATE TABLE full_universe_stage_audit(scan_time_utc TEXT, symbol TEXT, stage TEXT,
                         quote_volume REAL, day_change_pct REAL, rank REAL, shortlisted INTEGER, detail_json TEXT)""")
            for i, s in enumerate(("BTCUSDT", "ETHUSDT", "UNIUSDT")):
                a.execute("INSERT INTO full_universe_stage_audit VALUES('2026-10-13T09:00:00',?,?,?,0,0,0,'{}')",
                          (s, "NOT_SHORTLISTED" if s == "BTCUSDT" else "PREFILTER_ERROR", 100 - i))
        with sqlite3.connect(os.path.join(self.sd, "long_short_live_pool.db")) as l:
            l.execute("""CREATE TABLE events(id INTEGER PRIMARY KEY, event_time_utc TEXT, stage_to TEXT,
                         telegram_status TEXT, payload_json TEXT)""")
            l.execute("""CREATE TABLE confirmed_trade_outcomes(event_id INTEGER, venue TEXT, result TEXT,
                         net_return_pct REAL)""")
            ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
            l.execute("INSERT INTO events VALUES(1,?,?,?,?)", (ts, "CLOSE_CONFIRMED", "SENT",
                      json.dumps({"_live_price_source": "BINANCE_SPOT"})))
            l.execute("INSERT INTO confirmed_trade_outcomes VALUES(9,'BINANCE_SPOT','STOP',-1.1)")
        self.env = {k_: os.environ.pop(k_, None) for k_ in ("LS_ANALYST_DB", "LS_LIVE_DB")}
        self.addCleanup(lambda: [os.environ.__setitem__(a, b) for a, b in self.env.items() if b])

    def test_report_counts_and_reasons(self):
        r = h.build(self.sd, now_ms=self.now)
        self.assertEqual(r["scan_window"], 3)
        self.assertEqual(r["scan_stage_counts"], {"NOT_SHORTLISTED": 1, "PREFILTER_ERROR": 2})
        self.assertEqual(r["prefilter_data_ready"], 1)
        self.assertEqual(r["deep_data_ready"], 1)
        self.assertEqual(r["deep_ready_symbols"], ["BTCUSDT"])
        eth = r["per_symbol"]["ETHUSDT"]["5m"]
        self.assertEqual((eth["reason"], eth["contiguous_tail_bars"]), ("gap", 4))
        self.assertEqual(eth["latest_gap"]["missing_bars"], 1)
        self.assertEqual(eth["eta_minutes"], (120 - 4) * 5)
        self.assertEqual(r["per_symbol"]["UNIUSDT"]["15m"]["reason"], "invalid_source_or_fields")
        self.assertEqual(r["not_ready_reasons"]["5m:gap"], 1)

    def test_signal_cohorts_are_reported_by_price_source(self):
        s = h.build(self.sd, now_ms=self.now)["signals"]
        self.assertEqual(s["transitions_stage|telegram|price_source"], {"CLOSE_CONFIRMED|SENT|BINANCE_SPOT": 1})
        self.assertIn("BINANCE_SPOT", s["confirmed_outcomes_by_venue"])

    def test_missing_databases_do_not_crash(self):
        with tempfile.TemporaryDirectory() as empty:
            r = h.build(empty)
        self.assertEqual(r["kline_db"], "missing")
        self.assertEqual(r["symbols_checked"], 0)


if __name__ == "__main__":
    unittest.main()
