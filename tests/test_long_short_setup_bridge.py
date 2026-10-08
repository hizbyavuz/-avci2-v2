import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

import long_short_setup_bridge as bridge
import long_short_setup_lifecycle as life
import long_short_live_pool as live

T=datetime(2026,10,8,0,0,tzinfo=timezone.utc)


def x(side="LONG",setup_type="BREAKOUT",scan=T,**kw):
    obj={
        "symbol":"ETHUSDT","direction":side,"setup_type":setup_type,
        "trigger_level":100.0,"retest_low":99.8,"retest_high":100.2,
        "invalidation":98.0 if side=="LONG" else 102.0,
        "target1":103.0 if side=="LONG" else 97.0,
        "target2":106.0 if side=="LONG" else 94.0,
        "radar_only":False,
        "structure_gate":{"_v3_setup_type":setup_type},
        "scan_time":scan.isoformat(),
        "data_mode":"MULTI_VENUE_PUBLIC",
        "data_cohort":"SPOT_PLUS_GATE",
        "derivatives_provider":"GATE_FUTURES",
        "derivatives_quality":"FULL",
        "htf_direction":side,"htf_score":0,
        "htf_reasons":[],"confidence":60
    }
    obj.update(kw)
    return obj


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(":memory:")
        life.init_schema(self.db)

    def tearDown(self):
        self.db.close()

    def test_bridge_starts_watch_with_locked_levels(self):
        sid=bridge.observe_candidate(self.db,x(),T)
        self.assertIsNotNone(sid)
        a=life.actionable_setup_for_symbol(self.db,"ETHUSDT")
        self.assertEqual(a["state"],"WATCH")
        self.assertEqual(a["trigger_level"],100.0)
        self.assertEqual(len(a["config_hash"]),64)

    def test_bridge_observes_retest_but_does_not_fake_legacy_active(self):
        bridge.observe_candidate(self.db,x(),T)
        bridge.observe_live_stage(self.db,"ETHUSDT","WATCH","CLOSE_CONFIRMED",
            T+timedelta(minutes=5),100.1)
        bridge.observe_live_stage(self.db,"ETHUSDT","CLOSE_CONFIRMED","RETESTING",
            T+timedelta(minutes=6),100)
        bridge.observe_live_stage(self.db,"ETHUSDT","RETESTING","TRIGGERED",
            T+timedelta(minutes=7),103,telegram_status="SENT")
        state=life.actionable_setup_for_symbol(self.db,"ETHUSDT")
        self.assertEqual(state["state"],"CONFIRMED")
        self.assertIsNone(state["active_at"])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM setup_outcomes").fetchone()[0],0)
        reasons=[r[0] for r in self.db.execute("SELECT reason FROM setup_events")]
        self.assertIn("V3_1_RETEST_ZONE",reasons)
        self.assertIn("LEGACY_FINAL_NOT_EXECUTABLE_ACTIVE",reasons)

    def test_shadow_radar_never_produces_setup(self):
        self.assertIsNone(bridge.observe_candidate(self.db,x(radar_only=True),T))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM setups").fetchone()[0],0)

    def test_expired_watch_uses_documented_exit(self):
        bridge.observe_candidate(self.db,x(),T)
        self.assertTrue(bridge.retire_from_watchlist(self.db,"ETHUSDT",
                       "WATCHLIST_GRACE_EXPIRED",T+timedelta(minutes=20)))
        self.assertEqual(self.db.execute("SELECT state FROM setups").fetchone()[0],"WATCHLIST_EXPIRED")

    def test_live_telegram_levels_are_exactly_watch_levels(self):
        obj=x()
        data={
            "symbol":obj["symbol"],"direction":obj["direction"],
            "trigger_level":obj["trigger_level"],"retest_low":obj["retest_low"],
            "retest_high":obj["retest_high"],"invalidation":obj["invalidation"],
            "target1":obj["target1"],"target2":obj["target2"],
            "data_mode":obj["data_mode"],"structure_gate_json":"{}"
        }
        msg=live.message_for(data,"TRIGGERED",100.1,100.1)
        self.assertIn("Tetik seviyesi: "+live.fmtp(data["trigger_level"]),msg)
        self.assertIn("SL: "+live.fmtp(data["invalidation"]),msg)
        self.assertIn("TP1: "+live.fmtp(data["target1"]),msg)
        self.assertIn("TP2: "+live.fmtp(data["target2"]),msg)
        self.assertIsNone(live.message_for(data,"APPROACHING",100.1,100.1))
        self.assertIsNone(live.message_for(data,"CLOSE_CONFIRMED",100.1,100.1))

    def test_no_exchange_order_endpoint_in_setup_modules(self):
        root=Path(__file__).resolve().parents[1]
        for name in ("long_short_setup_lifecycle.py","long_short_setup_bridge.py",
                     "long_short_setup_outcomes.py"):
            src=(root/name).read_text()
            self.assertNotIn("/fapi/v1/"+ "order",src)
            self.assertNotIn("/api/v3/"+ "order",src)
            self.assertNotIn("/futures/usdt/"+ "orders",src)


if __name__=="__main__":
    unittest.main()
