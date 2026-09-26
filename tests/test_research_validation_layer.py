import sqlite3
import unittest

import research_validation_layer as rv


class ResearchValidationLayerTests(unittest.TestCase):
    def synthetic_rows(self):
        rows=[]
        periods=[
            ("2026-09-24T10:00:00+00:00","DISC"),
            ("2026-09-26T00:00:00+00:00","CAL"),
            ("2026-09-26T13:00:00+00:00","FINAL"),
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
        th=rv.attribution(c,"BINANCE",rows)
        families=rv.correlations(c,"BINANCE",rows,th)
        rv.significance(c,"BINANCE",rows)
        rv.baselines(c,"BINANCE",rows)
        rv.portfolio(c,"BINANCE",rows)
        rv.regimes(c,"BINANCE",rows)
        rv.drift(c,"BINANCE",rows,th)
        rv.precision_recall(c,"BINANCE",rows)
        rv.kelly(c,"BINANCE",rows)
        rv.latency(c,"BINANCE")
        self.assertGreater(len(th),0)
        self.assertGreaterEqual(families,1)
        self.assertGreater(c.execute("SELECT COUNT(*) FROM research_feature_attribution").fetchone()[0],1)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM research_significance").fetchone()[0],3)
        self.assertGreater(c.execute("SELECT COUNT(*) FROM research_portfolio_metrics").fetchone()[0],0)
        self.assertGreater(c.execute("SELECT COUNT(*) FROM research_regime_stats").fetchone()[0],0)
        self.assertGreater(c.execute("SELECT COUNT(*) FROM research_precision_recall").fetchone()[0],0)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM research_kelly").fetchone()[0],3)

    def test_split_is_frozen_by_time(self):
        self.assertEqual(rv.split_of("2026-09-24T00:00:00+00:00"),"DISCOVERY")
        self.assertEqual(rv.split_of("2026-09-26T00:00:00+00:00"),"CALIBRATION")
        self.assertEqual(rv.split_of("2026-09-26T13:00:00+00:00"),"FINAL_TEST")


if __name__=="__main__":
    unittest.main()
