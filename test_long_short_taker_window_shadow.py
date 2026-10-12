import os
import sqlite3
import tempfile
import unittest

from long_short_taker_window_shadow import summarize, SOURCE

class TakerWindowTests(unittest.TestCase):
    def test_missing_minute_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            p=os.path.join(d,"trades.db")
            with sqlite3.connect(p) as c:
                c.execute("CREATE TABLE taker_trades(event_ms INTEGER,symbol TEXT,source TEXT,signed_quote REAL)")
                c.executemany("INSERT INTO taker_trades VALUES(?,?,?,?)", [
                    (120000,"BTCUSDT",SOURCE,100.0),
                    (121000,"BTCUSDT",SOURCE,-50.0),
                ])
            result=summarize(p,["BTCUSDT"],end_ms=240000,minutes=2)["BTCUSDT"]
            self.assertFalse(result["ready"])
            self.assertEqual(result["reason"],"MISSING_MINUTE_COVERAGE")

    def test_two_complete_minutes(self):
        with tempfile.TemporaryDirectory() as d:
            p=os.path.join(d,"trades.db")
            with sqlite3.connect(p) as c:
                c.execute("CREATE TABLE taker_trades(event_ms INTEGER,symbol TEXT,source TEXT,signed_quote REAL)")
                c.executemany("INSERT INTO taker_trades VALUES(?,?,?,?)", [
                    (120000,"BTCUSDT",SOURCE,100.0),
                    (121000,"BTCUSDT",SOURCE,-50.0),
                    (180000,"BTCUSDT",SOURCE,200.0),
                    (181000,"BTCUSDT",SOURCE,-100.0),
                ])
            result=summarize(p,["BTCUSDT"],end_ms=240000,minutes=2)["BTCUSDT"]
            self.assertTrue(result["ready"])
            self.assertEqual(result["taker_buy_sell_ratio"],2.0)

if __name__=="__main__":
    unittest.main()
