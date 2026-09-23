import unittest

from gate_btc_divergence import _regime


class GateBtcDivergenceTests(unittest.TestCase):
    def test_down_regime_on_clear_hourly_drop(self):
        self.assertEqual(_regime({"btc_1h_pct": -0.6, "btc_15m_pct": 0.1}), "DOWN")

    def test_down_regime_on_combined_negative_move(self):
        self.assertEqual(_regime({"btc_1h_pct": -0.1, "btc_15m_pct": -0.4}), "DOWN")

    def test_sideways_when_move_is_small(self):
        self.assertEqual(_regime({"btc_1h_pct": -0.1, "btc_15m_pct": -0.1}), "SIDEWAYS")


if __name__ == "__main__":
    unittest.main()
