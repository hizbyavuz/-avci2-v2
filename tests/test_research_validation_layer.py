import sqlite3
import unittest

import research_validation_layer as rv
import research_p0_governance as p0
import research_decision_discipline as rd
import avci_global_governance as gg


class ResearchValidationLayerTests(unittest.TestCase):
    def synthetic_rows(self):
        rows=[]
        periods=[
            ("2026-09-20T10:00:00+00:00","DISC"),
            ("2026-09-28T00:00:00+00:00","CAL"),
            ("2026-10-05T13:00:00+00:00","FINAL"),
        ]
        idx=0
        for stamp,_ in periods:
            for i in range(20):
                cand=i%2==0
                high=i%4 in (0,1)
                hit=1 if (cand and high) or (not cand and i%7==0) else 0
                rows.append({
                    "key":str(idx),
                    "time":stamp,
                    "asset":f"A{idx}",
                    "group":"CANDIDATE" if cand else "RANDOM_CONTROL",
                    "regime":"SIDEWAYS" if i<10 else "UP",
                    "net":5.0 if hit else -2.0,
                    "hit":hit,
                    "score":6.0 if cand else 2.0,
                    "features":{
                        "volume_z_15m":4.0 if high else 1.0,
                        "retention_proxy":0.8 if high else 0.3,
                        "taker_buy_ratio_15m":0.62 if high else 0.45,
                    },
                })
                idx+=1
        return rows

    def test_full_research_pipeline_tables(self):
        c=sqlite3.connect(":memory:")
        c.row_factory=sqlite3.Row
        rv.init(c)
        rows=self.synthetic_rows()
        rv.store_splits(c,"BINANCE",rows)
        rv.build_purged_folds(c,"BINANCE",rows)
        th=rv.attribution(c,"BINANCE",rows)
        families=rv.correlations(c,"BINANCE",rows,th)
        rv.significance(c,"BINANCE",rows)
        rv.baselines(c,"BINANCE",rows)
        rv.portfolio(c,"BINANCE",rows)
        rv.regimes(c,"BINANCE",rows)
        rv.drift(c,"BINANCE",rows,th)
        rv.precision_recall(c,"BINANCE",rows)
        c.execute("""CREATE TABLE daily_movers(
            trade_date TEXT,symbol TEXT,change_24h REAL,quote_volume_24h REAL,
            rank_value INTEGER,config_version TEXT,created_at_utc TEXT)""")
        c.execute("INSERT INTO daily_movers VALUES(?,?,?,?,?,?,?)",
                  ("2026-10-05","A40",45.0,1000000,1,"x","2026-10-05T23:00:00+00:00"))
        c.execute("INSERT INTO daily_movers VALUES(?,?,?,?,?,?,?)",
                  ("2026-10-05","MISS",50.0,1000000,2,"x","2026-10-05T23:00:00+00:00"))
        rv.mover_recall(c,"BINANCE",rows)
        rv.kelly(c,"BINANCE",rows)
        rv.latency(c,"BINANCE")
        self.assertGreater(len(th),0)
        self.assertGreaterEqual(families,1)
        self.assertGreater(c.execute("SELECT COUNT(*) FROM research_feature_attribution").fetchone()[0],1)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM research_significance").fetchone()[0],3)
        self.assertGreater(c.execute("SELECT COUNT(*) FROM research_portfolio_metrics").fetchone()[0],0)
        self.assertGreater(c.execute("SELECT COUNT(*) FROM research_regime_stats").fetchone()[0],0)
        self.assertGreater(c.execute("SELECT COUNT(*) FROM research_precision_recall").fetchone()[0],0)
        self.assertGreater(c.execute("SELECT COUNT(*) FROM research_purged_folds").fetchone()[0],0)
        mr=c.execute("SELECT opportunity_n,caught_n FROM research_mover_recall WHERE split='FINAL_TEST'").fetchone()
        self.assertEqual(tuple(mr),(2,1))
        self.assertEqual(c.execute("SELECT COUNT(*) FROM research_kelly").fetchone()[0],3)

    def test_split_is_frozen_by_time(self):
        self.assertEqual(rv.split_of("2026-09-20T00:00:00+00:00"),"DISCOVERY")
        self.assertEqual(rv.split_of("2026-09-24T00:00:00+00:00"),"PURGED_EMBARGO")
        self.assertEqual(rv.split_of("2026-09-28T00:00:00+00:00"),"CALIBRATION")
        self.assertEqual(rv.split_of("2026-10-01T00:00:00+00:00"),"PURGED_EMBARGO")
        self.assertEqual(rv.split_of("2026-10-05T13:00:00+00:00"),"FINAL_TEST")


    def test_p0_two_way_dependence(self):
        c=sqlite3.connect(":memory:")
        p0.init(c)
        rows=[]
        for i in range(24):
            rows.append({
                "asset":f"A{i%4}",
                "time":rv.dt(f"2026-09-{10+(i%12):02d}T12:00:00+00:00"),
                "group":"CANDIDATE" if i%2==0 else "RANDOM_CONTROL",
                "regime":"SIDEWAYS",
                "net":3.0 if i%2==0 else -1.0,
            })
        selector=lambda r:r["group"]=="CANDIDATE"
        d=p0.two_way_cluster_diff(rows,selector)
        self.assertAlmostEqual(d["diff"],4.0)
        self.assertGreaterEqual(d["asset_clusters"],4)
        self.assertGreaterEqual(d["week_clusters"],2)
        self.assertIsNotNone(d["ci_low"])
        self.assertIsNotNone(d["ci_high"])
        self.assertEqual(c.execute("SELECT COUNT(*) FROM research_p0_dependence").fetchone()[0],0)


    def test_predeclared_mde_diff(self):
        rows=[
            {"grp":"CANDIDATE","net":2.0,"t":"2026-10-10T00:00:00+00:00","regime":"UP"},
            {"grp":"CANDIDATE","net":1.0,"t":"2026-10-11T00:00:00+00:00","regime":"UP"},
            {"grp":"RANDOM_CONTROL","net":0.2,"t":"2026-10-10T00:00:00+00:00","regime":"UP"},
            {"grp":"NEAR_MISS","net":0.0,"t":"2026-10-11T00:00:00+00:00","regime":"UP"},
        ]
        self.assertAlmostEqual(rd.holdout_diff_mean("BINANCE",rows),1.4)
        self.assertEqual(rd.GO_LIVE["min_candidate_control_expectancy_diff_pct"],0.50)

    def test_global_beta_and_concentration_helpers(self):
        p=[0.01,0.02,-0.01,0.03,0.00]
        b=[0.005,0.01,-0.005,0.015,0.00]
        self.assertAlmostEqual(gg.beta(p,b),2.0,places=6)
        self.assertAlmostEqual(gg.hhi({"BINANCE":3,"GATE":1}),0.625,places=6)



if __name__=="__main__":
    unittest.main()
