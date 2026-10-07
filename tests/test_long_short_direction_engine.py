import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from long_short_direction_engine import decide_direction
import long_short_live_pool as live


class DirectionEvidenceTests(unittest.TestCase):
    def test_one_opposing_soft_feature_does_not_kill_strong_short(self):
        out=decide_direction(
            long_score=0,
            short_score=69,
            deriv_ready=True,
            actionable_liquidity_ok=True,
            external_only_unverified=False,
            spot_flow={"available":True,"delta_share":0.35},
            residual={"residual_3h_pct":-0.10},
            phase="BALANCE",
        )
        self.assertEqual(out["direction"],"SHORT")
        self.assertTrue(out["eligible"])
        self.assertGreater(out["short_score"],out["long_score"])

    def test_missing_derivatives_is_still_hard_safety_block(self):
        out=decide_direction(
            long_score=80,
            short_score=10,
            deriv_ready=False,
            actionable_liquidity_ok=True,
            external_only_unverified=False,
            spot_flow={"available":True,"delta_share":0.30},
            residual={"residual_3h_pct":0.50},
            phase="IMPULSE",
        )
        self.assertEqual(out["direction"],"LONG")
        self.assertFalse(out["eligible"])
        self.assertIn("derivatives_incomplete",out["hard_blockers"])

    def test_weak_edge_stays_neutral(self):
        out=decide_direction(
            long_score=58,
            short_score=51,
            deriv_ready=True,
            actionable_liquidity_ok=True,
            external_only_unverified=False,
            spot_flow={"available":False},
            residual={"residual_3h_pct":0.0},
            phase="BALANCE",
        )
        self.assertEqual(out["direction"],"NONE")
        self.assertFalse(out["eligible"])

    def test_impulse_prefers_pullback_entry_style(self):
        out=decide_direction(
            long_score=75,
            short_score=5,
            deriv_ready=True,
            actionable_liquidity_ok=True,
            external_only_unverified=False,
            spot_flow={"available":True,"delta_share":0.10},
            residual={"residual_3h_pct":0.20},
            phase="IMPULSE",
        )
        self.assertEqual(out["direction"],"LONG")
        self.assertEqual(out["setup_type"],"PULLBACK")


class LiveEntryEvidenceTests(unittest.TestCase):
    class Row(dict):
        def keys(self):
            return super().keys()

    def row(self,direction="LONG"):
        return self.Row({
            "direction":direction,
            "trigger_level":100.0,
            "structure_gate_json":json.dumps({
                "version":live.STRUCTURE_GATE_VERSION,
                "_v3_precheck_ok":True,
                "_v3_setup_type":"BREAKOUT",
                "room_pct":1.5,
                "timeframe_confluence":2,
                "level_touches":3,
            }),
        })

    def test_four_of_five_soft_checks_can_confirm(self):
        early={
            "closed_5m_open":99.7,
            "closed_5m_high":101.2,
            "closed_5m_low":99.5,
            "closed_5m_close":100.8,
            "closed_5m_body_ratio":0.65,
            "closed_5m_close_location":0.76,
            "closed_5m_volume_mult":0.95,  # only failing soft check
            "recent_closed_5m_closes":[99.8,100.3,100.8],
        }
        out=live.live_structure_confirmation(self.row("LONG"),100.8,early)
        self.assertTrue(out["qualified"])
        self.assertEqual(out["quality_score"],4)
        self.assertFalse(out["volume_ok"])

    def test_three_of_five_soft_checks_do_not_confirm(self):
        early={
            "closed_5m_open":100.7,  # red candle -> direction fail
            "closed_5m_high":101.2,
            "closed_5m_low":99.8,
            "closed_5m_close":100.5,
            "closed_5m_body_ratio":0.14,  # body fail
            "closed_5m_close_location":0.50,
            "closed_5m_volume_mult":1.30,
            "recent_closed_5m_closes":[99.8,100.2,100.5],
        }
        out=live.live_structure_confirmation(self.row("LONG"),100.5,early)
        self.assertFalse(out["qualified"])
        self.assertLess(out["quality_score"],4)

    def test_close_must_still_be_beyond_locked_trigger(self):
        early={
            "closed_5m_open":99.0,
            "closed_5m_high":100.2,
            "closed_5m_low":98.8,
            "closed_5m_close":99.9,
            "closed_5m_body_ratio":0.64,
            "closed_5m_close_location":0.79,
            "closed_5m_volume_mult":1.50,
            "recent_closed_5m_closes":[99.5,99.7,99.9],
        }
        out=live.live_structure_confirmation(self.row("LONG"),99.9,early)
        self.assertFalse(out["qualified"])
        self.assertFalse(out["beyond_trigger"])


if __name__=="__main__":
    unittest.main()
