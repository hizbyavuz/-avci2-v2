"""Read-only Railway readiness audit: coverage, gaps and stale data."""
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from long_short_futures_readiness_audit import inspect

class FuturesReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db=os.path.join(self.tmp.name,"futures.db")
        with sqlite3.connect(self.db) as con:
            con.execute("""CREATE TABLE futures_closed_klines(
                symbol TEXT, interval TEXT, open_ms INTEGER,
                close_ms INTEGER, source TEXT)""")
            now=1700000000000
            for interval,count,step in (("1m",30,60000),("5m",30,300000),("1h",25,3600000)):
                last_open=now-step
                for i in range(count):
                    start=last_open-(count-i-1)*step
                    con.execute("INSERT INTO futures_closed_klines VALUES(?,?,?,?,?)",
                        ("BTCUSDT",interval,start,start+step-1,
                         "BINANCE_UM_FUTURES_WEBSOCKET"))
        self.now=1700000000000

    def test_complete_contiguous_closed_data_ready(self):
        result=inspect(self.db,"BTCUSDT",self.now)
        self.assertTrue(result["ready"])
        self.assertEqual(result["reason"],"OK")

    def test_missing_database_fails_closed(self):
        self.assertEqual(inspect(self.db+".missing","BTCUSDT",self.now)["reason"],
                         "FUTURES_DB_MISSING")

    def test_stale_hourly_data_fails_closed(self):
        result=inspect(self.db,"BTCUSDT",self.now+8000000)
        self.assertFalse(result["ready"])
        self.assertFalse(result["intervals"]["1h"]["ready"])

    def test_gapped_data_fails_closed(self):
        with sqlite3.connect(self.db) as con:
            con.execute("DELETE FROM futures_closed_klines WHERE interval='5m' AND open_ms=(SELECT MIN(open_ms) FROM futures_closed_klines WHERE interval='5m')")
        result=inspect(self.db,"BTCUSDT",self.now)
        self.assertFalse(result["ready"])
        self.assertEqual(result["intervals"]["5m"]["bars"],29)

if __name__=="__main__":
    unittest.main()
