#!/usr/bin/env python3
import json
import os
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import long_short_analyst as a
import long_short_validation as v


def fake_kline(open_ms, close_ms, px):
    return [
        open_ms, str(px), str(px*1.001), str(px*0.999), str(px),
        "1", close_ms, "1000", "10", "0.5", "500", "0"
    ]


class ClosedCandleTests(unittest.TestCase):
    def test_fetch_klines_drops_forming_bar(self):
        now_ms=int(time.time()*1000)
        rows=[]
        start=now_ms-25*60_000
        for i in range(24):
            o=start+i*60_000
            rows.append(fake_kline(o,o+59_999,100+i))
        # forming bar closes in the future and must never reach timeframe_features
        rows.append(fake_kline(now_ms-10_000,now_ms+50_000,999))
        with patch.object(a,"fget",return_value=rows):
            k=a.fetch_klines("BTCUSDT","1m",25)
        self.assertEqual(len(k["close"]),24)
        self.assertNotEqual(k["close"][-1],999.0)


class UniverseSelectionTests(unittest.TestCase):
    def test_activity_shortlist_does_not_reserve_slots_for_volume_leaders(self):
        rows=[
            {"symbol":"BTCUSDT","rank":8.0,"day_change":1.0,"quote_volume":10_000_000_000},
            {"symbol":"ETHUSDT","rank":7.0,"day_change":1.5,"quote_volume":8_000_000_000},
            {"symbol":"RLCUSDT","rank":30.0,"day_change":60.0,"quote_volume":1_200_000_000},
            {"symbol":"CAPUSDT","rank":24.0,"day_change":38.0,"quote_volume":100_000_000},
            {"symbol":"NMRUSDT","rank":20.0,"day_change":36.0,"quote_volume":110_000_000},
        ]
        got=a.select_deep_shortlist(rows,3)
        self.assertEqual([x["symbol"] for x in got],["RLCUSDT","CAPUSDT","NMRUSDT"])


class PersistenceTests(unittest.TestCase):
    def test_save_scan_paper_insert_schema(self):
        old_db=a.DB
        try:
            with tempfile.TemporaryDirectory() as td:
                a.DB=os.path.join(td,"a.db")
                a.init_db()
                ts=datetime.now(timezone.utc).isoformat()
                payload={
                    "multi_venue_derivatives":{
                        "critical_fields":["oi_change_1h","funding_pct","taker_ratio","long_short_ratio","depth_imbalance"],
                        "oi_change_1h":1.0,"funding_pct":0.01,"taker_ratio":1.1,
                        "long_short_ratio":1.0,"depth_imbalance":0.1,"errors":[],
                    },
                    "data_mode":"BINANCE_SPOT_GRAPH_ONLY",
                    "derivatives_source":"MULTI_VENUE_PUBLIC",
                    "derivatives_quality":"FULL",
                    "derivatives_coverage":1.0,
                    "derivatives_funding_pct":0.01,
                    "next_funding_time_ms":int((time.time()+3600)*1000),
                    "funding_interval_hours":8.0,
                    "execution_proxy":{"costs":{"250":{"buy_bps":3.0,"sell_bps":3.0}}},
                }
                row=a.Analysis(
                    "BTCUSDT","LONG",70,10,70,100.0,99.9,100.1,98.0,104.0,106.0,2.0,
                    ["x"],[],payload
                )
                a.save_scan(ts,"UP",1,[row])
                with sqlite3.connect(a.DB) as con:
                    got=con.execute("""SELECT episode_id,btc_regime,paper_notional_usdt,
                                             next_funding_time_ms,funding_interval_hours
                                      FROM paper_setups""").fetchone()
                self.assertIsNotNone(got)
                self.assertEqual(got[1],"UP")
                self.assertAlmostEqual(float(got[2]),a.PAPER_NOTIONAL_USDT)
                self.assertGreater(float(got[3]),0)
                self.assertEqual(float(got[4]),8.0)
        finally:
            a.DB=old_db


class ForwardValidationTests(unittest.TestCase):
    def test_model_direction_and_ablation_are_measured_separately(self):
        con=sqlite3.connect(":memory:")
        v.init_db(con)
        con.execute("""CREATE TABLE analyses(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_time_utc TEXT,symbol TEXT,status TEXT,long_score INTEGER,
            short_score INTEGER,payload_json TEXT
        )""")
        scan=(datetime.now(timezone.utc)-timedelta(hours=6)).isoformat()
        con.execute("""INSERT INTO universe_observations(
            scan_time_utc,symbol,quote_volume,day_change_pct,prefilter_rank,
            shortlisted,direction_hint,reference_price,risk_pct,t5_structure,
            t15_structure,t15_change_4,t15_vol_mult,t15_atr_pct,
            breakout20,breakdown20,payload_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (scan,"BTCUSDT",1e9,1,10,1,"SHORT",100.0,2.0,1,1,1,1,1,1,0,"{}"))
        payload={
            "market_regime":"UP",
            "setup_plan":{"triggered":True,"direction":"LONG"},
            "ablations":{
                "trend":{"decision":"WAIT"},
                "derivatives":{"decision":"LONG"},
            },
        }
        con.execute("""INSERT INTO analyses(
            scan_time_utc,symbol,status,long_score,short_score,payload_json
        ) VALUES(?,?,?,?,?,?)""",(scan,"BTCUSDT","LONG",70,10,json.dumps(payload)))
        con.commit()

        path={"high":104.0,"low":98.0,"close":103.0,"bars":15}
        with patch.object(v,"future_path",return_value=path):
            inserted=v.evaluate_due(con)
        self.assertEqual(inserted,3)
        row=con.execute("""SELECT direction,model_direction,breakout_direction,
                                 model_r_multiple,breakout_r_multiple,ablation_r_json
                          FROM forward_validation
                          WHERE horizon_min=15""").fetchone()
        self.assertEqual(row[0],"SHORT")
        self.assertEqual(row[1],"LONG")
        self.assertEqual(row[2],"LONG")
        self.assertGreater(row[3],0)
        self.assertGreater(row[4],0)
        abl=json.loads(row[5])
        self.assertNotIn("trend",abl)
        self.assertIn("derivatives",abl)
        con.close()


if __name__=="__main__":
    unittest.main()
