#!/usr/bin/env python3
import sqlite3
import unittest
from datetime import datetime, timezone

import long_short_research_validation as r


class ResearchValidationTests(unittest.TestCase):
    def test_cohort_mapping_is_not_mixed(self):
        self.assertEqual(
            r.cohort_from_payload({"derivatives_source":"BINANCE_FUTURES"}),
            "BINANCE_FUTURES_NATIVE",
        )
        self.assertEqual(
            r.cohort_from_payload({
                "derivatives_source":"MULTI_VENUE_PUBLIC",
                "derivatives_selected_provider":"BYBIT_LINEAR",
            }),
            "SPOT_PLUS_BYBIT",
        )
        self.assertEqual(
            r.cohort_from_payload({
                "derivatives_source":"MULTI_VENUE_PUBLIC",
                "derivatives_selected_provider":"GATE_FUTURES",
            }),
            "SPOT_PLUS_GATE",
        )

    def test_episode_cluster_is_two_hours_and_directional(self):
        a=datetime(2026,10,5,10,5,tzinfo=timezone.utc)
        b=datetime(2026,10,5,11,55,tzinfo=timezone.utc)
        self.assertEqual(r.episode_id(a,"LONG"),r.episode_id(b,"LONG"))
        self.assertNotEqual(r.episode_id(a,"LONG"),r.episode_id(b,"SHORT"))

    def test_bootstrap_counts_episodes_not_raw_signals(self):
        rows=[
            {"episode_id":"A","delta_r":1.0},
            {"episode_id":"A","delta_r":-1.0},
            {"episode_id":"B","delta_r":0.5},
        ]
        out=r.bootstrap_episode_ci(rows,"delta_r")
        self.assertEqual(out["episodes"],2)
        self.assertAlmostEqual(out["mean"],0.25,places=8)

    def test_execution_cost_is_directionally_conservative(self):
        path={
            "entry_open":100.0,"entry_high":100.5,"entry_low":99.5,"entry_close":100.0,
            "exit_open":101.0,"exit_high":101.5,"exit_low":100.5,"exit_close":101.0,
        }
        long=r.cost_adjusted_result("LONG",path,1.0,{})
        short=r.cost_adjusted_result("SHORT",path,1.0,{})
        self.assertLess(long["net_return_pct"],1.0)
        self.assertLess(short["net_return_pct"],0.0)
        self.assertGreaterEqual(long["entry_slippage_bps"],r.MIN_SLIPPAGE_BPS_PER_SIDE)
        self.assertGreaterEqual(long["exit_slippage_bps"],r.MIN_SLIPPAGE_BPS_PER_SIDE)

    def test_same_minute_barrier_is_stop_first(self):
        path={"bar_path":[{"high":102.0,"low":98.0,"close":101.0,"close_time_ms":123}]}
        out=r.barrier_outcome("LONG",path,99.0,101.0,102.0)
        self.assertEqual(out["outcome"],"STOP")
        out=r.barrier_outcome("SHORT",path,101.0,99.0,98.0)
        self.assertEqual(out["outcome"],"STOP")

    def test_funding_is_applied_only_when_settlement_is_crossed(self):
        start=datetime(2026,10,5,10,0,tzinfo=timezone.utc)
        path={
            "entry_time":start,
            "exit_time":start.replace(hour=11),
            "entry_open":100.0,"entry_high":100.2,"entry_low":99.8,"entry_close":100.0,
            "exit_open":100.0,"exit_high":100.2,"exit_low":99.8,"exit_close":100.0,
        }
        payload={
            "derivatives_funding_pct":0.05,
            "next_funding_time_ms":int(start.replace(minute=30).timestamp()*1000),
        }
        paid=r.cost_adjusted_result("LONG",path,1.0,payload)
        nofund=r.cost_adjusted_result("LONG",path,1.0,{})
        self.assertLess(paid["net_return_pct"],nofund["net_return_pct"])

    def test_protocol_meta_refuses_mutation(self):
        con=sqlite3.connect(":memory:")
        frozen=r.init_db(con)
        self.assertEqual(frozen["primary_cohort"],"BINANCE_FUTURES_NATIVE")
        con.execute("UPDATE protocol_meta SET value='999' WHERE key='primary_horizon_min'")
        with self.assertRaises(RuntimeError):
            r.init_db(con)
        con.close()


if __name__=="__main__":
    unittest.main()
