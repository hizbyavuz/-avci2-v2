import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from long_short_live_pool import live_structure_confirmation, STRUCTURE_GATE_VERSION


def _row(direction="LONG", trigger=100.0):
    return {
        "direction": direction,
        "trigger_level": trigger,
        "structure_gate_json": json.dumps({
            "version": STRUCTURE_GATE_VERSION,
            "qualified_precheck": True,
            "room_pct": 0.8,
            "timeframe_confluence": 3,
            "level_touches": 2,
        }),
    }


class StructureGateV2Tests(unittest.TestCase):
    def test_strong_long_breakout_passes(self):
        q=live_structure_confirmation(_row(),102.0,{
            "closed_5m_open":99.8,
            "closed_5m_high":102.3,
            "closed_5m_low":99.7,
            "closed_5m_close":102.0,
            "closed_5m_body_ratio":0.846,
            "closed_5m_close_location":0.885,
            "closed_5m_volume_mult":1.45,
        })
        self.assertTrue(q["qualified"])
        self.assertFalse(q["fake_breakout"])

    def test_long_wick_fake_breakout_is_rejected(self):
        q=live_structure_confirmation(_row(),99.7,{
            "closed_5m_open":99.5,
            "closed_5m_high":100.8,
            "closed_5m_low":99.2,
            "closed_5m_close":99.7,
            "closed_5m_body_ratio":0.125,
            "closed_5m_close_location":0.3125,
            "closed_5m_volume_mult":1.80,
        })
        self.assertFalse(q["qualified"])
        self.assertTrue(q["fake_breakout"])

    def test_weak_volume_breakout_is_rejected(self):
        q=live_structure_confirmation(_row(),101.2,{
            "closed_5m_open":99.7,
            "closed_5m_high":101.4,
            "closed_5m_low":99.6,
            "closed_5m_close":101.2,
            "closed_5m_body_ratio":0.833,
            "closed_5m_close_location":0.889,
            "closed_5m_volume_mult":0.82,
        })
        self.assertFalse(q["qualified"])
        self.assertFalse(q["volume_ok"])


if __name__=="__main__":
    unittest.main()
