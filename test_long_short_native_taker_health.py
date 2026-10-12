import os
import sqlite3
import tempfile
import unittest
from long_short_native_taker_health import check, SOURCE

class NativeTakerHealthTests(unittest.TestCase):
    def test_missing_db_fails_closed(self):
        self.assertFalse(check("/nonexistent/native.db","BTCUSDT",now_ms=200000)["ready"])
    def test_fresh_both_sides(self):
        with tempfile.TemporaryDirectory() as d:
            path=os.path.join(d,"native.db")
            with sqlite3.connect(path) as c:
                c.execute("CREATE TABLE taker_trades(symbol TEXT,source TEXT,event_ms INTEGER,signed_quote REAL)")
                c.executemany("INSERT INTO taker_trades VALUES(?,?,?,?)",[
                    ("BTCUSDT",SOURCE,190000,100.0),
                    ("BTCUSDT",SOURCE,195000,-50.0),
                    ("BTCUSDT","OTHER",195000,-10000.0)])
            r=check(path,"BTCUSDT",now_ms=200000)
            self.assertTrue(r["ready"])
            self.assertEqual(r["taker_buy_sell_ratio"],2.0)
    def test_stale_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            path=os.path.join(d,"native.db")
            with sqlite3.connect(path) as c:
                c.execute("CREATE TABLE taker_trades(symbol TEXT,source TEXT,event_ms INTEGER,signed_quote REAL)")
                c.executemany("INSERT INTO taker_trades VALUES(?,?,?,?)",[
                    ("BTCUSDT",SOURCE,1000,100.0),("BTCUSDT",SOURCE,1000,-50.0)])
            self.assertFalse(check(path,"BTCUSDT",now_ms=300000)["ready"])
if __name__=="__main__":
    unittest.main()
