import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from datetime import datetime,timezone
from long_short_context_audit import archived_context,volume_context,ro,summarize,trend

class ContextAuditTests(unittest.TestCase):
    def setUp(self):
        self.c=sqlite3.connect(":memory:");self.c.row_factory=sqlite3.Row
        self.c.execute("CREATE TABLE analyses(symbol,scan_time_utc,payload_json)")
        self.sent=1791935169.613424
    def tearDown(self):self.c.close()
    def add(self,epoch,direction):
        p={"v3":{"direction":direction},"t1h":{"price":3,"ema20":2,"ema50":1}}
        self.c.execute("INSERT INTO analyses VALUES(?,?,?)",("X",datetime.fromtimestamp(epoch,timezone.utc).isoformat(),json.dumps(p)))
    def test_future_and_stale_scans_cannot_match(self):
        self.add(self.sent+1,"LONG");self.add(self.sent-601,"SHORT")
        self.assertEqual(archived_context(self.c,"X",self.sent)["status"],"NO_RECENT_ARCHIVED_SCAN")
    def test_latest_past_scan_and_availability_are_distinct(self):
        self.add(self.sent-500,"SHORT");self.add(self.sent-10,"LONG");self.add(self.sent+1,"SHORT")
        x=archived_context(self.c,"X",self.sent)
        self.assertEqual(x["direction"],"LONG")
        self.assertFalse(x["availability_at_delivery_verified"])
        self.assertEqual(x["trend_1h"],"UP")
    def candles(self):
        self.c.execute("CREATE TABLE futures_closed_klines(symbol,interval,open_ms,close_ms,quote_volume,source)")
        hour=3600000;end=int(self.sent*1000)//hour*hour
        for i in range(168):
            t=end-(168-i)*hour
            self.c.execute("INSERT INTO futures_closed_klines VALUES(?,?,?,?,?,?)",("X","1h",t,t+hour-1,1+i//24,"BINANCE_UM_FUTURES_REST"))
        self.c.execute("INSERT INTO futures_closed_klines VALUES(?,?,?,?,?,?)",("X","1h",end,end+hour-1,999999,"BINANCE_UM_FUTURES_REST"))
    def test_open_hour_excluded_and_five_day_offset_correct(self):
        self.candles();x=volume_context(self.c,"X",self.sent)
        self.assertEqual(x["quote_volume_24h_blocks_oldest_first"],[24,48,72,96,120,144,168])
        self.assertEqual(x["last24_vs_5days_earlier24"],3.5)
    def test_one_missing_hour_invalidates_comparison(self):
        self.candles();self.c.execute("DELETE FROM futures_closed_klines WHERE open_ms=(SELECT MIN(open_ms) FROM futures_closed_klines)")
        self.assertEqual(volume_context(self.c,"X",self.sent)["status"],"INCOMPLETE_7D_HOURLY_PATH")
    def test_invalid_source_and_nonfinite_volume_rejected(self):
        self.candles();self.c.execute("UPDATE futures_closed_klines SET source='SPOT'")
        self.assertEqual(volume_context(self.c,"X",self.sent)["status"],"UNVERIFIED_HOURLY_PATH")
        self.c.execute("UPDATE futures_closed_klines SET source='BINANCE_UM_FUTURES_REST',quote_volume=?",(float("inf"),))
        self.assertEqual(volume_context(self.c,"X",self.sent)["status"],"UNVERIFIED_HOURLY_PATH")
    def test_read_only_does_not_create_or_write(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/"db"
            with self.assertRaises(FileNotFoundError):ro(p)
            self.assertFalse(p.exists())
            sqlite3.connect(p).close()
            with ro(p) as c:
                with self.assertRaises(sqlite3.OperationalError):c.execute("CREATE TABLE forbidden(x)")
    def test_short_baseline_and_terminal_exclusion(self):
        def row(side,pct,include=True):
            return {"direction":side,"included_as_direction_idea":include,
                    "outcomes":{"15":{"status":"RESOLVED","direction_pct":pct}}}
        x=summarize([row("SHORT",1),row("LONG",-1),row("LONG",100,False)])["15"]
        self.assertEqual(x["resolved"],2);self.assertEqual(x["right"],1)
        self.assertEqual(x["always_long_right_same_endpoints"],0)
        self.assertEqual(x["always_short_right_same_endpoints"],2)
    def test_unknown_trend_not_neutral_evidence(self):
        self.assertEqual(trend({}),"UNKNOWN")
        self.assertEqual(trend({"price":float("nan"),"ema20":1,"ema50":2}),"UNKNOWN")

if __name__=="__main__":unittest.main()
