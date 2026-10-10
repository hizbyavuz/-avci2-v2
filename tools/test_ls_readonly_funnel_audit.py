"""Synthetic, no-network tests for tools/ls_readonly_funnel_audit.py."""
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("ls_readonly_funnel_audit.py")

class AuditTest(unittest.TestCase):
    def test_paused_watch_diagnostics_snapshot_precedes_mutation(self):
        """Prevent the previous bug: reading active setups after they were paused."""
        source = (SCRIPT.parent.parent / "long_short_live_pool.py").read_text(encoding="utf-8")
        start = source.index("def sync_watchlist(items):")
        end = source.index("\\ndef _ema(", start) if "\\ndef _ema(" in source[start:] else source.index("\ndef _ema(", start)
        body = source[start:end]
        self.assertIn("active_at_start=", body)
        self.assertIn("paused_absent=", body)
        self.assertIn("paused_radar=", body)
        self.assertIn("actionable_now=", body)
        self.assertIn("downgraded_to_radar_only", body)
        self.assertLess(body.index("active_at_start="), body.index("for x in items:"))
        self.assertLess(body.index("paused_absent="), body.index("UPDATE watch_state SET analyst_active=0,last_update_utc=?"))

    def test_synthetic_funnel_and_no_db_changes(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state"
            state.mkdir()
            analyst = state / "long_short_analyst.db"
            live = state / "long_short_live_pool.db"
            with sqlite3.connect(analyst) as db:
                db.execute("CREATE TABLE analyses(scan_time_utc TEXT,symbol TEXT,status TEXT,payload_json TEXT,reasons_json TEXT,risks_json TEXT)")
                db.execute("INSERT INTO analyses VALUES(?,?,?,?,?,?)", (
                    "2026-10-10T11:10:00+00:00","BTCUSDT","WAIT",
                    json.dumps({"setup_plan":{"direction":"LONG","trigger_level":10}}),json.dumps(["waiting_for_close"]),json.dumps(["wide_spread"])))
            with sqlite3.connect(live) as db:
                db.execute("CREATE TABLE events(event_time_utc TEXT,symbol TEXT,stage_to TEXT,telegram_status TEXT,payload_json TEXT)")
                db.execute("INSERT INTO events VALUES(?,?,?,?,?)", (
                    "2026-10-10T11:12:00+00:00","BTCUSDT","EXECUTION_BLOCKED","SHADOW",
                    json.dumps({"_trigger_execution_gate":{"reason":"trigger_time_net_r_failed"}})))
                db.execute("CREATE TABLE watch_episodes(started_at_utc TEXT,end_reason TEXT,max_stage TEXT)")
                db.execute("INSERT INTO watch_episodes VALUES(?,?,?)", (
                    "2026-10-10T11:10:00+00:00","TRIGGER_EXECUTION_GATE","EXECUTION_BLOCKED"))
                db.execute("CREATE TABLE confirmed_trade_outcomes(entry_time_utc TEXT,result TEXT)")
                db.execute("CREATE TABLE watch_state(stage TEXT,analyst_active INTEGER,structure_gate_json TEXT)")
                db.execute("INSERT INTO watch_state VALUES(?,?,?)",("WATCH",0,json.dumps({"_radar_only":False})))
                db.execute("INSERT INTO watch_state VALUES(?,?,?)",("APPROACHING",0,json.dumps({"_radar_only":True})))
            before = {p.name:p.read_bytes() for p in (analyst,live)}
            result = subprocess.run([sys.executable,"-I",str(SCRIPT),
                "--state-dir",str(state),"--since","2026-10-10T11:09:00+00:00",
                "--out","/tmp/ls_funnel_synthetic_test.json"],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["analyst"]["rows"],1)
            self.assertEqual(report["analyst"]["explicit_reasons"]["waiting_for_close"],1)
            self.assertEqual(report["analyst"]["explicit_risks"]["wide_spread"],1)
            self.assertEqual(report["live"]["execution_block_reasons"]["trigger_time_net_r_failed"],1)
            self.assertEqual(report["episodes"]["end_reasons"]["TRIGGER_EXECUTION_GATE"],1)
            self.assertEqual(report["confirmed_outcomes"]["outcomes"],0)
            self.assertTrue(report["audit_complete"])
            self.assertEqual(report["performance_interpretation"],"INSUFFICIENT_CONFIRMED_OUTCOMES")
            self.assertEqual(report["watch_state_scope"],"CURRENT_SNAPSHOT_NOT_SINCE_FILTERED")
            self.assertEqual(report["watch_state"]["cohorts"]["ACTIONABLE_SETUP/PAUSED/WATCH"],1)
            self.assertEqual(report["watch_state"]["cohorts"]["RADAR/PAUSED/APPROACHING"],1)
            self.assertEqual(before,{p.name:p.read_bytes() for p in (analyst,live)})
            Path("/tmp/ls_funnel_synthetic_test.json").unlink(missing_ok=True)

    def test_pause_log_breakdown_is_not_trade_performance(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "railway.log"
            log.write_text(
                'LIVE_WATCH_PAUSED {"reason":"absent_from_latest_selected_watchlist","setups":[{"symbol":"ENAUSDT","direction":"LONG","stage":"WATCH"}]}\n'
                'LIVE_WATCH_PAUSED {"reason":"downgraded_to_radar_only","setups":[{"symbol":"MAGICUSDT","direction":"SHORT","stage":"APPROACHING"}]}\n'
                'LIVE_WATCH_PAUSED not-json\n',encoding="utf-8")
            import importlib.util
            spec=importlib.util.spec_from_file_location("audit_module",SCRIPT)
            module=importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            result=module.summarize_paused_watch_log(log.read_text().splitlines())
            self.assertEqual(result["pause_events_by_reason_stage"]["absent_from_latest_selected_watchlist/WATCH"],1)
            self.assertEqual(result["pause_events_by_reason_stage"]["downgraded_to_radar_only/APPROACHING"],1)
            self.assertEqual(result["malformed_messages"],1)
            self.assertIn("not missed trades",result["warning"])

    def test_missing_databases_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            result = subprocess.run([sys.executable,"-I",str(SCRIPT),
                "--state-dir",td,"--since","2026-10-10T11:09:00+00:00",
                "--out","/tmp/ls_funnel_missing_test.json"],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn("Missing database",result.stderr)

    def test_reject_state_dir_output(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            result = subprocess.run([sys.executable,"-I",str(SCRIPT),
                "--state-dir",str(state),"--since","2026-10-10T11:09:00+00:00",
                "--out",str(state/"result.json")],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertFalse((state/"result.json").exists())

if __name__ == "__main__":
    unittest.main()
