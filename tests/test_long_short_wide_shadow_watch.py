import json
import sqlite3
import tempfile
import unittest
from datetime import datetime,timezone
from pathlib import Path
from long_short_wide_shadow_watch import audit

class WideShadowTests(unittest.TestCase):
    def test_failed_room_remains_observable_but_not_actionable(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);stamp=int(datetime(2026,10,8,3,0,tzinfo=timezone.utc).timestamp()*1000)
            state=p/"state.json"
            state.write_text(json.dumps({"version":"NATIVE_BINANCE_UM_MARKET_WATCH_V1",
                "history":{"TESTUSDT":[[stamp-61000,100],[stamp-1000,100.15]],
                           "OTHERUSDT":[[stamp-1000,25]]}}))
            report=p/"report.json"
            report.write_text(json.dumps({"source":"BINANCE_FUTURES_UM_WS","timestamp_ms":stamp,"issues":[]}))
            db=p/"db.sqlite"
            with sqlite3.connect(db) as c:
                c.execute("CREATE TABLE analyses(scan_time_utc TEXT,symbol TEXT,status TEXT,payload_json TEXT)")
                payload={"setup_plan":{"direction":"LONG","trigger_level":100.2},
                         "v3":{"eligible":False},"derivatives_ready":True,
                         "structure_gate":{"room_ok":False,"rr_ok":True,
                             "level_ok":True,"qualified_precheck":False}}
                dt=datetime.fromtimestamp((stamp-180000)/1000,timezone.utc)
                c.execute("INSERT INTO analyses VALUES(?,?,?,?)",(dt.isoformat(),"TESTUSDT","NO_TRADE",json.dumps(payload)))
            r=audit(str(db),str(state),str(report))
            self.assertEqual(r["funnel"]["deep_scanned"],1)
            self.assertEqual(r["funnel"]["room_failed"],1)
            self.assertEqual(r["funnel"]["shadow_native_setups"],1)
            self.assertEqual(r["shadow_all_count"],1)
            self.assertEqual(r["shadow_approaching"][0]["stage"],"BEFORE_TRIGGER")
            self.assertFalse(r["shadow_approaching"][0]["is_trade_signal"])
            self.assertEqual(r["telegram_messages"],0)
    def test_reject_wrong_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/"report").write_text(json.dumps({"source":"BINANCE_SPOT","timestamp_ms":0}))
            with self.assertRaisesRegex(ValueError,"NON_NATIVE_FUTURES"):
                audit(str(p/"missing"),str(p/"missing"),str(p/"report"))
    def test_expired_analyst_scan_does_not_create_shadow(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);t=int(datetime(2026,10,8,3,0,tzinfo=timezone.utc).timestamp()*1000)
            (p/"state").write_text(json.dumps({"version":"NATIVE_BINANCE_UM_MARKET_WATCH_V1","history":{}}))
            (p/"report").write_text(json.dumps({"source":"BINANCE_FUTURES_UM_WS","timestamp_ms":t,"issues":[]}))
            with sqlite3.connect(str(p/"db")) as c:
                c.execute("CREATE TABLE analyses(scan_time_utc TEXT,symbol TEXT,status TEXT,payload_json TEXT)")
                c.execute("INSERT INTO analyses VALUES(?,?,?,?)",("2026-10-07T00:00:00+00:00","TESTUSDT","WAIT","{}"))
            r=audit(str(p/"db"),str(p/"state"),str(p/"report"))
            self.assertIn("ANALYST_SCAN_TOO_OLD_OR_FUTURE",r["issues"])
            self.assertEqual(r["shadow_all_count"],0)
