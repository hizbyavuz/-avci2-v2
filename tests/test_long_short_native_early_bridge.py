import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from datetime import datetime,timezone,timedelta

from long_short_native_early_bridge import bridge,analyst_scan

class NativeEarlyBridgeTests(unittest.TestCase):
    def test_native_market_to_existing_prefilter_matching(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            ms=int(datetime(2026,10,8,2,40,tzinfo=timezone.utc).timestamp()*1000)
            history={"version":"NATIVE_BINANCE_UM_MARKET_WATCH_V1",
                     "history":{
                       "UPUSDT":[[ms-301000,100],[ms-1000,102]],
                       "DOWNUSDT":[[ms-301000,100],[ms-1000,97]],
                       "MISSINGUSDT":[[ms-301000,100],[ms-1000,101.5]],
                       "STALEUSDT":[[ms-301000,100],[ms-100000,110]]}}
            state=folder/"state.json";state.write_text(json.dumps(history))
            market=folder/"market.json"
            market.write_text(json.dumps({"source":"BINANCE_FUTURES_UM_WS",
                                          "timestamp_ms":ms,"issues":[]}))
            db=folder/"analyst.db"
            with sqlite3.connect(db) as c:
                c.execute("""CREATE TABLE universe_observations(
                  scan_time_utc TEXT,symbol TEXT,shortlisted INTEGER,quote_volume REAL,
                  day_change_pct REAL,direction_hint TEXT,prefilter_rank REAL)""")
                scan=datetime.fromtimestamp(ms/1000,timezone.utc)-timedelta(minutes=3)
                for sym,shortlisted in (("UPUSDT",0),("DOWNUSDT",1)):
                    c.execute("INSERT INTO universe_observations VALUES(?,?,?,?,?,?,?)",
                      (scan.isoformat(),sym,shortlisted,30_000_000,0,"LONG",10))
            result=bridge(state,market,db)
            rows={o["symbol"]:o for o in result["observations"] if o["window_minutes"]==5}
            self.assertEqual(result["active_futures_symbols_with_price"],3)
            self.assertEqual(len(rows),3)
            self.assertEqual(rows["UPUSDT"]["analyst_coverage"],"PREFILTER_ONLY")
            self.assertEqual(rows["DOWNUSDT"]["analyst_coverage"],"IN_ANALYST_DEEP_SCAN")
            self.assertEqual(rows["MISSINGUSDT"]["analyst_coverage"],"NOT_IN_ANALYST_PREFILTER")
            self.assertEqual(rows["UPUSDT"]["observation_band"],"1_TO_3_OBSERVATION")
            self.assertEqual(rows["DOWNUSDT"]["direction_observed"],"SHORT")
            self.assertFalse(rows["DOWNUSDT"]["trade_signal"])
            self.assertEqual(result["counts"]["5"]["not_in_analyst_prefilter"],1)
    def test_source_not_native_futures_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            (p/"s").write_text(json.dumps({"version":"NATIVE_BINANCE_UM_MARKET_WATCH_V1","history":{}}))
            (p/"r").write_text(json.dumps({"source":"BINANCE_SPOT","timestamp_ms":10000}))
            with self.assertRaisesRegex(ValueError,"NATIVE_FUTURES_SOURCE_NOT_VALIDATED"):
                bridge(p/"s",p/"r",p/"missing")
    def test_missing_analyst_db_is_explicit(self):
        _,_,err=analyst_scan("/nonexistent/path/integration-test.db",3000000)
        self.assertEqual(err,"ANALYST_DB_NOT_FOUND")

if __name__=="__main__":
    unittest.main()
