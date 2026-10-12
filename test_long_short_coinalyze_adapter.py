import unittest
from unittest.mock import patch
import long_short_coinalyze_adapter as a

class AdapterTests(unittest.TestCase):
    def setUp(self):
        a._cache.clear()

    @patch.object(a, "_get")
    def test_rejects_other_exchange(self, get):
        get.return_value = [{"symbol":"BTCUSDT_PERP.A","exchange":"B",
            "symbol_on_exchange":"BTCUSDT","is_perpetual":True,
            "quote_asset":"USDT","margined":"STABLE"}]
        result = a.snapshot("BTCUSDT")
        self.assertIsNone(result["oi_now"])
        self.assertIsNone(result["oi_change_1h"])
        self.assertFalse(result["oi_fresh"])
        self.assertEqual(get.call_count, 1)

    @patch.object(a, "_get")
    def test_stale_oi_never_accepted(self, get):
        import time
        get.side_effect = [
            [{"symbol":"BTCUSDT_PERP.A","exchange":"A",
              "symbol_on_exchange":"BTCUSDT","is_perpetual":True,
              "quote_asset":"USDT","margined":"STABLE",
              "oi_lq_vol_denominated_in":"BASE_ASSET",
              "has_long_short_ratio_data":False}],
            [{"symbol":"BTCUSDT_PERP.A","update":int(time.time())-9999,"value":100}],
        ]
        result = a.snapshot("BTCUSDT")
        self.assertFalse(result["oi_fresh"])
        self.assertIsNone(result["oi_change_1h"])

if __name__ == "__main__":
    unittest.main()
