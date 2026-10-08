"""Offline, deterministic V4 safety tests: math, provenance, collector, data health."""
import sqlite3
import unittest

from long_short_v4_paper_math import linear_return_pct, path_result
from long_short_v4_native_pipeline import initialize,ingest,choose_symbols,SOURCE
from long_short_v4_research import setup,feature_at,contiguous
from long_short_v4_structure import (
    oi_engine,resample_complete_5m,previous_utc_day_week_levels,
    swing_zones,robust_signal_calibration
)

def bar(t,op=100,hi=101,lo=99,cl=100,source="BINANCE_SPOT"):
    return dict(open_ms=t,open=op,high=hi,low=lo,close=cl,closed=True,source=source)

class PaperMathTests(unittest.TestCase):
    def test_linear_short(self):
        self.assertAlmostEqual(linear_return_pct("SHORT",100,90),10)
        self.assertAlmostEqual(linear_return_pct("SHORT",100,110),-10)
        self.assertAlmostEqual(linear_return_pct("LONG",100,110),10)

    def test_stop_first_in_same_bar(self):
        r=path_result("LONG",100,95,105,110,[bar(0,100,111,94,103)],funding_pct=0)
        self.assertEqual(r["first_barrier"],"STOP")
        self.assertFalse(r["tp2_after_tp1_without_stop"])

    def test_tp2_only_after_prior_tp1_and_before_stop(self):
        r=path_result("SHORT",100,106,95,90,[
            bar(0,100,100,94,96),bar(60000,96,97,89,90)],funding_pct=0)
        self.assertEqual(r["first_barrier"],"TP1")
        self.assertTrue(r["tp2_after_tp1_without_stop"])
        self.assertAlmostEqual(r["gross_pct"],5)
        s=path_result("SHORT",100,106,95,90,[
            bar(0,100,100,94,96),bar(60000,96,107,89,100)],funding_pct=0)
        self.assertFalse(s["tp2_after_tp1_without_stop"])

    def test_gap_and_missing_funding_are_not_silent(self):
        g=path_result("LONG",100,95,105,110,[bar(0),bar(120000)])
        self.assertEqual(g["status"],"DATA_FAILURE")
        self.assertEqual(g["reason"],"MISSING_CANDLE")
        s=path_result("LONG",100,95,105,110,[bar(0)],funding_pct=None)
        self.assertIsNone(s["net_pct"])
        self.assertFalse(s["funding_known"])

    def test_mixed_source_rejected(self):
        x=path_result("LONG",100,95,105,110,[
            bar(0,source="GATE_FUTURES"),bar(60000,source="BINANCE_SPOT")])
        self.assertEqual(x["reason"],"MIXED_VENUE")

class NativeIngestTests(unittest.TestCase):
    def setUp(self):
        self.c=sqlite3.connect(":memory:")
        initialize(self.c)
    def tearDown(self):self.c.close()

    def test_mark_liquidation_kline_and_dedupe(self):
        m={"e":"markPriceUpdate","E":300000,"s":"BTCUSDT","p":"100",
           "i":"101","r":"0.0001","T":360000}
        self.assertEqual(ingest(self.c,"t","market","!markPrice@arr",m,300100),1)
        self.assertEqual(ingest(self.c,"t","market","!markPrice@arr",m,300100),0)
        l={"e":"forceOrder","E":300100,"o":{"s":"BTCUSDT","S":"SELL",
            "ap":"100","z":"2"}}
        ingest(self.c,"t","market","!forceOrder@arr",l,300200)
        self.assertEqual(self.c.execute("SELECT notional FROM liquidations").fetchone()[0],200)
        k={"e":"kline","E":300100,"s":"BTCUSDT",
           "k":{"x":True,"s":"BTCUSDT","i":"5m","t":0,"T":299999,
                "o":"100","h":"103","l":"99","c":"102","v":"10",
                "q":"1010","V":"8","Q":"810","n":10}}
        ingest(self.c,"t","market","btcusdt@kline_5m",k,300200)
        self.assertEqual(self.c.execute("SELECT taker_buy_base FROM closed_klines").fetchone()[0],8)

    def test_book_and_trade_signs(self):
        b={"e":"bookTicker","E":6000,"s":"BTCUSDT","b":"99.9","a":"100.1","B":"1","A":"3"}
        ingest(self.c,"t","public","btcusdt@bookTicker",b,6010)
        self.assertGreater(self.c.execute("SELECT spread_bps FROM book_top").fetchone()[0],0)
        x={"e":"aggTrade","E":6000,"T":6000,"s":"BTCUSDT","p":"100","q":"2","m":False}
        ingest(self.c,"t","market","btcusdt@aggTrade",x,6010)
        self.assertEqual(self.c.execute("SELECT signed_quote FROM taker_trades").fetchone()[0],200)

    def test_cm_filter_and_symbol_selection(self):
        m={"e":"markPriceUpdate","st":2,"E":300000,"s":"BTCUSDT","p":"100"}
        self.assertEqual(ingest(self.c,"t","market","!markPrice@arr",m,300100),0)
        self.assertEqual(choose_symbols("BTCUSDT,ETHUSDT,INVALID,ETHUSDT"),["BTCUSDT","ETHUSDT"])

    def test_feature_does_not_invent_oi(self):
        setup(self.c)
        f=feature_at(self.c,"BTCUSDT",300000)
        self.assertIsNone(f["external_oi_change_1h"])
        self.assertFalse(f["binance_native_oi_available"])
        self.assertFalse(f["signal_authorized"])
        self.assertIn("MISSING_1M_PATH",f["quality_reasons"])

class StructureTests(unittest.TestCase):
    def test_oi_engine_is_observation_not_signal(self):
        self.assertEqual(oi_engine(1,-2),"PRICE_UP_OI_DOWN_SHORT_COVER")
        self.assertEqual(oi_engine(-1,2),"PRICE_DOWN_OI_UP_NEW_SHORT_RISK")
        self.assertEqual(oi_engine(2,None),"UNKNOWN")
    def test_complete_15m_aggregation_requires_three_closed_5m(self):
        rows=[{"open_ms":j*300000,"open":100,"high":102,"low":98,
               "close":101,"volume":10,"source":SOURCE}
               for j in range(3)]
        self.assertEqual(len(resample_complete_5m(rows,15)),1)
        self.assertEqual(resample_complete_5m(rows[:2],15),[])
        rows[1]["open_ms"]=123
        self.assertEqual(resample_complete_5m(rows,15),[])
    def test_missing_day_week_not_fabricated(self):
        x=previous_utc_day_week_levels([],1_700_000_000_000)
        self.assertIsNone(x["previous_day"]["high"])
        self.assertIsNone(x["previous_week"]["low"])
    def test_calibration_requires_independent_episodes(self):
        e=[{"episode_id":"same_wave","net_r":1} for _ in range(500)]
        result=robust_signal_calibration(e)
        self.assertEqual(result["independent_episodes"],1)
        self.assertFalse(result["edge_proven"])

if __name__=="__main__":
    unittest.main()
