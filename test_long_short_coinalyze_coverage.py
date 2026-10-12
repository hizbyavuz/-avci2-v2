import unittest
from unittest.mock import patch
from long_short_coinalyze_coverage import coverage

class CoverageTests(unittest.TestCase):
    @patch("long_short_coinalyze_coverage._get")
    def test_only_binance_usdm_perpetual(self, get):
        get.return_value=[
            {"symbol":"BTCUSDT_PERP.A","symbol_on_exchange":"BTCUSDT","exchange":"A",
             "is_perpetual":True,"quote_asset":"USDT","margined":"STABLE"},
            {"symbol":"ETHUSDT_PERP.B","symbol_on_exchange":"ETHUSDT","exchange":"B",
             "is_perpetual":True,"quote_asset":"USDT","margined":"STABLE"}]
        r=coverage(["BTCUSDT","ETHUSDT","BTCUSDT"])
        self.assertEqual(r["covered"],["BTCUSDT"])
        self.assertEqual(r["missing"],["ETHUSDT"])
        self.assertEqual(r["requested_count"],2)
        get.assert_called_once_with("/future-markets")
if __name__=="__main__":
    unittest.main()
