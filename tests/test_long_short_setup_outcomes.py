import sqlite3
import sys
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

import long_short_setup_lifecycle as life
import long_short_setup_outcomes as out

T=datetime(2026,10,8,0,0,tzinfo=timezone.utc)


def bars_at(start,prices):
    rows=[]
    for i,(o,h,l,c) in enumerate(prices):
        begin=int((start+timedelta(minutes=i)).timestamp()*1000)
        rows.append([begin,str(o),str(h),str(l),str(c),"0",begin+59999])
    return rows


def flat(start=T+timedelta(minutes=10),n=180,price=100):
    return bars_at(start,[(price,price+.1,price-.1,price) for _ in range(n)])


def setup(side="LONG",at=T+timedelta(minutes=10)):
    return {
        "setup_id":"abc","symbol":"ETHUSDT","direction":side,
        "setup_type":"BREAKOUT","trigger_level":100,
        "retest_low":99.8,"retest_high":100.2,
        "invalidation":98 if side=="LONG" else 102,
        "tp1":103 if side=="LONG" else 97,
        "tp2":106 if side=="LONG" else 94,
        "active_at":at.isoformat(),"entry_price":100,
        "final_telegram_event_id":123,"final_telegram_sent_at":at.isoformat(),
        "price_source":"GATE_FUTURES"
    }


class OutcomeUnitTests(unittest.TestCase):
    def setUp(self):
        self.end=T+timedelta(minutes=25)

    def test_01_first_barrier_tp1(self):
        prices=[(100,100.2,99.8,100)]*3+[(100,103.2,99.9,103)]+[(100,100,100,100)]*11
        label=out.label_path(setup(),bars_at(T+timedelta(minutes=10),prices),15,now=self.end)
        self.assertEqual(label["outcome_status"],"TP1")
        self.assertEqual(label["exit_price"],103)
        self.assertAlmostEqual(label["net_return_pct"],2.7)

    def test_02_stop_first_on_same_candle(self):
        prices=[(100,103.8,97.5,102)]+[(100,100,100,100)]*14
        label=out.label_path(setup(),bars_at(T+timedelta(minutes=10),prices),15,now=self.end)
        self.assertEqual(label["outcome_status"],"STOP")
        self.assertEqual(label["exit_price"],98)

    def test_03_gap_down_exit_at_worse_open_not_imaginary_stop(self):
        prices=[(96,97,94,95)]+[(100,100,100,100)]*14
        label=out.label_path(setup(),bars_at(T+timedelta(minutes=10),prices),15,now=self.end)
        self.assertEqual(label["outcome_status"],"STOP")
        self.assertEqual(label["exit_price"],96)
        self.assertAlmostEqual(label["net_return_pct"],-4.3)

    def test_04_short_gap_up_exit_at_worse_open(self):
        prices=[(105,107,104,106)]+[(100,100,100,100)]*14
        label=out.label_path(setup("SHORT"),bars_at(T+timedelta(minutes=10),prices),15,now=self.end)
        self.assertEqual(label["outcome_status"],"STOP")
        self.assertEqual(label["exit_price"],105)

    def test_05_timeout_uses_fixed_clock_bar_not_late_job(self):
        prices=[(100,100.1,99.9,100)]*14+[(100,100.1,99.9,100.05)]
        prices += [(100,200,50,175)]*10
        label=out.label_path(setup(),bars_at(T+timedelta(minutes=10),prices),15,now=T+timedelta(hours=2))
        self.assertEqual(label["outcome_status"],"TIMEOUT")
        self.assertAlmostEqual(label["exit_price"],100.05)
        self.assertEqual(label["exit_at"],self.end.isoformat())
        self.assertAlmostEqual(label["net_return_pct"],-0.25)

    def test_06_missing_bar_is_data_gap_not_loss(self):
        rows=flat()
        rows.pop(4)
        label=out.label_path(setup(),rows,15,now=self.end)
        self.assertEqual(label["outcome_status"],"DATA_GAP")
        self.assertIsNone(label["net_return_pct"])
        self.assertIsNone(label["exit_price"])

    def test_07_cost_stress_double_round_trip(self):
        label=out.label_path(setup(),flat(),15,now=self.end)
        self.assertEqual(label["outcome_status"],"TIMEOUT")
        self.assertAlmostEqual(label["net_return_pct"],-0.3)
        self.assertAlmostEqual(label["net_return_2x_cost_pct"],-0.6)

    def test_08_only_matured_horizon(self):
        with self.assertRaisesRegex(ValueError,"not matured"):
            out.label_path(setup(),flat(),15,now=T+timedelta(minutes=24))

    def test_09_no_premature_entry_or_non_telegram_performance(self):
        for key in ("active_at","final_telegram_event_id","final_telegram_sent_at"):
            s=setup();s[key]=None
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    out.label_path(s,flat(),15,now=self.end)

    def test_10_no_entry_minute_lookahead(self):
        s=setup(at=T+timedelta(minutes=10,seconds=30))
        prices=[(100,104,97,100)] + [(100,100.1,99.9,100)]*15
        label=out.label_path(s,bars_at(T+timedelta(minutes=10),prices),15,
            now=T+timedelta(minutes=26))
        self.assertEqual(label["outcome_status"],"TIMEOUT")

    def test_11_duplicate_minute_creates_data_gap(self):
        rows=flat()
        rows.insert(4,rows[4])
        label=out.label_path(setup(),rows,15,now=self.end)
        self.assertEqual(label["outcome_status"],"DATA_GAP")

    def test_12_active_only_db_and_independence_in_2_hours(self):
        with sqlite3.connect(":memory:") as db:
            life.init_schema(db)
            d={
                "symbol":"ETHUSDT","direction":"LONG","setup_type":"BREAKOUT",
                "trigger_level":100,"retest_low":99.8,"retest_high":100.2,
                "invalidation":98,"tp1":103,"tp2":106,
                "source_scan":T,"regime_at_create":"DOWN","config_hash":"a"*64
            }
            sid,_=life.create_candidate(db,d,T)
            life.transition(db,sid,"WATCH",T)
            self.assertEqual(out.primary_report(db),[])
            life.transition(db,sid,"CONFIRMED",T+timedelta(minutes=1))
            active=T+timedelta(minutes=10)
            life.transition(db,sid,"ACTIVE",active,observed_price=100,
                final_telegram_event_id=1,final_telegram_sent_at=active)
            self.assertTrue(out.write_outcome(db,sid,15,flat(),now=self.end,price_source="GATE_FUTURES",same_chart_venue=True))
            self.assertFalse(out.write_outcome(db,sid,15,flat(),now=self.end))
            life.transition(db,sid,"TP2",self.end)
            d["source_scan"]=T+timedelta(minutes=30)
            sid2,_=life.create_candidate(db,d,T+timedelta(minutes=30))
            life.transition(db,sid2,"WATCH",T+timedelta(minutes=30))
            life.transition(db,sid2,"CONFIRMED",T+timedelta(minutes=31))
            active2=T+timedelta(minutes=40)
            life.transition(db,sid2,"ACTIVE",active2,observed_price=100,
                final_telegram_event_id=2,final_telegram_sent_at=active2)
            self.assertEqual(life.independence_key(db,sid2),sid)
            rows=out.primary_report(db)
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]["raw_final_events"],1)
            self.assertEqual(rows[0]["unique_market_clusters"],1)
            self.assertEqual(rows[0]["direction"],"LONG")
            self.assertEqual(rows[0]["conclusion"],"INCONCLUSIVE")


if __name__=="__main__":
    unittest.main()
