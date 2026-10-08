"""Offline V4 TESTNET ONLY execution contract tests. No network and no orders."""
import copy
import os
import sqlite3
import unittest
from unittest.mock import patch

from long_short_v4_execution_gate import (
    RiskPolicy,assess,reserve,init_journal,change_state,quantity_round_down
)
from long_short_v4_demo_adapter import (
    FuturesDemoClient,DemoRejected,demo_intents,execute_demo_with_rollback
)

NOW=20_000_000
def scenario():
    plan={"setup_id":"btc-plan-1","symbol":"BTCUSDT","state":"TRIGGERED",
          "direction":"LONG","source":"BINANCE_FUTURES_UM_WS",
          "analyst_authorized":True,"stop_and_tp_locked":True,
          "derivatives_quality_ok":True,"data_cohort":"VENUE_LABELED_EXTERNAL",
          "signal_time_ms":NOW-10000,"entry":100.0,"stop":99.0,
          "tp1":104.0,"tp2":108.0}
    market={"symbol":"BTCUSDT","source":"BINANCE_FUTURES_UM_WS",
        "data_health_ok":True,"complete_history":True,"closed_candle_only":True,
        "event_ms":NOW-1000,"closed_candle_time_ms":NOW-5000,
        "spread_bps":3.0,"estimated_slippage_bps":6.0,
        "step_size":0.001,"min_qty":0.001,"min_notional":5.0}
    account={"one_way_mode":True,"isolated_margin":True,
        "account_reconciled":True,"kill_switch":False,"unresolved_order":False,
        "unprotected_position":False,"open_positions":0,
        "realized_loss_today_usdt":0,"equity_usdt":1000,
        "leverage":2,"venue_permitted":True}
    return plan,market,account

class GateTests(unittest.TestCase):
    def test_clean_paper_decision_bounded(self):
        p,m,a=scenario()
        x=assess(p,m,a,now_ms=NOW)
        self.assertTrue(x.approved,x.reasons)
        self.assertLessEqual(x.notional_usdt,50)
        self.assertLessEqual(x.risk_usdt,1)
        self.assertEqual(x.planned_qty,"0.500")
    def test_short_stop_target_order(self):
        p,m,a=scenario()
        p.update(direction="SHORT",stop=101,tp1=96,tp2=92)
        x=assess(p,m,a,now_ms=NOW)
        self.assertTrue(x.approved,x.reasons)
    def test_reject_unverified_or_stale(self):
        p,m,a=scenario();p["source"]="BINANCE_SPOT"
        self.assertIn("NOT_NATIVE_FUTURES_PRICE",assess(p,m,a,now_ms=NOW).reasons)
        p,m,a=scenario();m["event_ms"]=NOW-90000
        self.assertIn("STALE_FEED",assess(p,m,a,now_ms=NOW).reasons)
        p,m,a=scenario();m["complete_history"]=False
        self.assertIn("HISTORY_HAS_GAPS",assess(p,m,a,now_ms=NOW).reasons)
    def test_stop_and_source_required(self):
        p,m,a=scenario();p["stop"]=101
        self.assertIn("INVALID_LEVELS",assess(p,m,a,now_ms=NOW).reasons)
        p,m,a=scenario();p["derivatives_quality_ok"]=False
        self.assertIn("DERIVATIVES_NOT_READY",assess(p,m,a,now_ms=NOW).reasons)
        p,m,a=scenario();m["source"]="GATE_FUTURES"
        self.assertIn("PRICE_SOURCE_MISMATCH",assess(p,m,a,now_ms=NOW).reasons)
    def test_no_open_positions_unknown_orders_or_kill(self):
        p,m,a=scenario();a["open_positions"]=1
        self.assertIn("MAX_CONCURRENT_POSITIONS",assess(p,m,a,now_ms=NOW).reasons)
        p,m,a=scenario();a["kill_switch"]=True
        self.assertIn("KILL_SWITCH_ON",assess(p,m,a,now_ms=NOW).reasons)
        p,m,a=scenario();a["unprotected_position"]=True
        self.assertIn("UNPROTECTED_POSITION",assess(p,m,a,now_ms=NOW).reasons)
        p,m,a=scenario();a["unresolved_order"]=True
        self.assertIn("UNRESOLVED_ORDER",assess(p,m,a,now_ms=NOW).reasons)
    def test_no_hedge_cross_or_unverified_venue(self):
        p,m,a=scenario();a["one_way_mode"]=False
        self.assertIn("REQUIRE_ONE_WAY_MODE",assess(p,m,a,now_ms=NOW).reasons)
        p,m,a=scenario();a["isolated_margin"]=False
        self.assertIn("REQUIRE_ISOLATED_MARGIN",assess(p,m,a,now_ms=NOW).reasons)
        p,m,a=scenario();a["venue_permitted"]=False
        self.assertIn("VENUE_ACCESS_NOT_VERIFIED",assess(p,m,a,now_ms=NOW).reasons)
    def test_only_paper_or_testnet(self):
        p,m,a=scenario()
        self.assertIn("MODE_NOT_ALLOWED",assess(p,m,a,mode="LIVE",now_ms=NOW).reasons)
    def test_quantity_rounding_never_rounds_up(self):
        self.assertEqual(quantity_round_down(10,3,"0.2"),"3.2")
    def test_idempotent_reserve(self):
        p,m,a=scenario();d=assess(p,m,a,now_ms=NOW)
        with sqlite3.connect(":memory:") as c:
            self.assertTrue(reserve(c,p,d,now_ms=NOW))
            self.assertFalse(reserve(c,p,d,now_ms=NOW+1))
            self.assertTrue(change_state(c,d.intent_id,"RESERVED","PAPER_OPEN"))
            self.assertFalse(change_state(c,d.intent_id,"RESERVED","PAPER_OPEN"))
    def test_unapproved_cannot_reserve(self):
        p,m,a=scenario();a["kill_switch"]=True;d=assess(p,m,a,now_ms=NOW)
        with sqlite3.connect(":memory:") as c:
            self.assertFalse(reserve(c,p,d,now_ms=NOW))

class DemoTests(unittest.TestCase):
    def test_hard_pinned_demo_domain(self):
        with self.assertRaises(ValueError):
            FuturesDemoClient(api_key="x",secret="y",base="https://fapi.binance.com")
    def test_always_dry_run_by_default(self):
        p,m,a=scenario();d=assess(p,m,a,now_ms=NOW)
        client=FuturesDemoClient(api_key="x",secret="y")
        result=execute_demo_with_rollback(client,d,p,send=False)
        self.assertEqual(result["status"],"DRY_RUN")
        self.assertEqual(result["orders_placed"],0)
    def test_stop_uses_new_binance_algo_endpoint(self):
        p,m,a=scenario();d=assess(p,m,a,now_ms=NOW)
        r=demo_intents(p,d)
        self.assertEqual(r["stop_path"],"/fapi/v1/algoOrder")
        self.assertEqual(r["stop"]["triggerPrice"],"99.0")
        self.assertNotIn("stopPrice",r["stop"])
        self.assertEqual(r["tp"]["type"],"TAKE_PROFIT_MARKET")
        self.assertEqual(r["stop"]["closePosition"],"true")
    def test_no_post_without_switch_even_with_keys(self):
        c=FuturesDemoClient(api_key="fake",secret="fake")
        with patch.dict(os.environ,{"LS_V4_TESTNET_SEND":"NO"}):
            with self.assertRaises(DemoRejected):
                c.signed_request("POST","/fapi/v1/order",{"symbol":"BTCUSDT"},send=True)
    def test_no_unsupported_host_or_methods(self):
        c=FuturesDemoClient(api_key="fake",secret="fake")
        with self.assertRaises(ValueError):
            c.signed_request("DELETE","/fapi/v1/order",{},send=True)
    def test_testnet_adapter_cannot_process_bad_signal(self):
        p,m,a=scenario();a["kill_switch"]=True
        d=assess(p,m,a,now_ms=NOW)
        with self.assertRaises(DemoRejected):
            demo_intents(p,d)

if __name__=="__main__":unittest.main()
