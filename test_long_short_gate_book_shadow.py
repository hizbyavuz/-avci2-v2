import unittest
from unittest.mock import patch
import requests
from long_short_gate_book_shadow import observe, _vwap

class Response:
    def __init__(self, payload): self.payload = payload
    def raise_for_status(self): pass
    def json(self): return self.payload

def fake_get(url, **kwargs):
    if "/contracts/" in url:
        return Response({"quanto_multiplier": "0.001"})
    return Response({"bids": [{"p": "100", "s": 5000}],
                     "asks": [{"p": "101", "s": 5000}]})

class TestShadow(unittest.TestCase):
    @patch("long_short_gate_book_shadow.requests.get", side_effect=fake_get)
    def test_multiplier_vwap_never_approves_execution(self, get):
        result = observe("BTCUSDT", 250)
        self.assertTrue(result["available"])
        self.assertFalse(result["execution_verified"])
        self.assertTrue(result["depth_sufficient"])
        self.assertAlmostEqual(result["buy_vwap"], 101)
        self.assertAlmostEqual(result["sell_vwap"], 100)
        self.assertEqual(get.call_args_list[0].kwargs["params"]["contract"], "BTC_USDT")

    def test_insufficient_depth(self):
        self.assertIsNone(_vwap([(100, 1)], 0.001, 250))

    def test_bad_symbol_fails_closed(self):
        self.assertFalse(observe("BTC/USDT")["available"])

    @patch("long_short_gate_book_shadow.requests.get", side_effect=requests.Timeout("network"))
    def test_network_failure_fails_closed(self, get):
        result = observe("BTCUSDT")
        self.assertFalse(result["available"])
        self.assertFalse(result["execution_verified"])

if __name__ == "__main__":
    unittest.main()
