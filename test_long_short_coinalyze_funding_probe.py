import unittest
from unittest.mock import patch
from long_short_coinalyze_funding_probe import funding_snapshot

class FundingTests(unittest.TestCase):
    @patch("long_short_coinalyze_funding_probe._cached_request")
    @patch("long_short_coinalyze_funding_probe._market")
    def test_fresh_funding(self, market, get):
        market.return_value=("BTCUSDT_PERP.A",{})
        get.return_value=[{"symbol":"BTCUSDT_PERP.A","value":0.01,"update":1000}]
        x=funding_snapshot("BTCUSDT",now=1010)
        self.assertTrue(x["funding_fresh"])
        self.assertEqual(x["funding_pct"],0.01)

    @patch("long_short_coinalyze_funding_probe._cached_request")
    @patch("long_short_coinalyze_funding_probe._market")
    def test_stale_funding(self, market, get):
        market.return_value=("BTCUSDT_PERP.A",{})
        get.return_value=[{"symbol":"BTCUSDT_PERP.A","value":0.01,"update":1000}]
        x=funding_snapshot("BTCUSDT",now=2000)
        self.assertFalse(x["funding_fresh"])
        self.assertIsNone(x["funding_pct"])

    @patch("long_short_coinalyze_funding_probe._market",return_value=None)
    def test_non_binance_market(self, market):
        self.assertIsNone(funding_snapshot("BADUSDT")["funding_pct"])

if __name__=="__main__":
    unittest.main()
