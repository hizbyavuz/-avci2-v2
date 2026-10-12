import unittest
from unittest.mock import patch
import coinalyze_binance_probe as probe

MARKET = {"symbol": "BTCUSDT_PERP.A", "exchange": "A", "symbol_on_exchange": "BTCUSDT", "is_perpetual": True, "quote_asset": "USDT", "margined": "STABLE", "oi_lq_vol_denominated_in": "BASE"}

class CoinalyzeProbeTests(unittest.TestCase):
    @patch.dict("os.environ", {"COINALYZE_API_KEY": "test-only"})
    @patch.object(probe.time, "time", return_value=10000)
    @patch.object(probe, "fetch")
    def test_fresh_binance_data(self, fetch, _):
        fetch.side_effect = [[MARKET], [{"symbol": "BTCUSDT_PERP.A", "update": 9970, "value": 100}], [{"symbol": "BTCUSDT_PERP.A", "history": [{"t": 9700, "r": 1.2}]}]]
        result = probe.probe(["BTCUSDT"])["BTCUSDT"]
        self.assertTrue(result["market_verified"])
        self.assertTrue(result["oi_ok"])
        self.assertTrue(result["ls_ok"])

    @patch.dict("os.environ", {"COINALYZE_API_KEY": "test-only"})
    @patch.object(probe, "fetch", return_value=[{**MARKET, "exchange": "B"}])
    def test_wrong_exchange_is_rejected(self, _):
        result = probe.probe(["BTCUSDT"])["BTCUSDT"]
        self.assertFalse(result["market_verified"])
        self.assertFalse(result["oi_ok"])

    @patch.dict("os.environ", {"COINALYZE_API_KEY": "test-only"})
    @patch.object(probe.time, "time", return_value=10000)
    @patch.object(probe, "fetch")
    def test_stale_data_is_rejected(self, fetch, _):
        fetch.side_effect = [[MARKET], [{"symbol": "BTCUSDT_PERP.A", "update": 9000, "value": 100}], [{"symbol": "BTCUSDT_PERP.A", "history": [{"t": 9000, "r": 1.2}]}]]
        result = probe.probe(["BTCUSDT"])["BTCUSDT"]
        self.assertFalse(result["oi_ok"])
        self.assertFalse(result["ls_ok"])

if __name__ == "__main__":
    unittest.main()
