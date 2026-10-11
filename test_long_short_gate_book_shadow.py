import unittest
from unittest.mock import patch
from long_short_gate_book_shadow import observe

class Response:
    def raise_for_status(self): pass
    def json(self):
        return {"bids":[{"p":"100","s":10}],"asks":[{"p":"101","s":10}]}

class TestShadow(unittest.TestCase):
    @patch("long_short_gate_book_shadow.requests.get", return_value=Response())
    def test_valid_observation_is_never_execution_approval(self, get):
        result=observe("BTCUSDT")
        self.assertTrue(result["available"])
        self.assertFalse(result["execution_verified"])
        self.assertGreater(result["spread_bps"],0)
        self.assertEqual(get.call_args.kwargs["params"]["contract"],"BTC_USDT")
    def test_bad_symbol_fails_closed(self):
        self.assertFalse(observe("BTC/USDT")["available"])
    @patch("long_short_gate_book_shadow.requests.get", side_effect=Exception("network"))
    def test_unexpected_failure(self, get):
        # Unexpected programming failures should surface rather than silently approve.
        with self.assertRaises(Exception):
            observe("BTCUSDT")

if __name__ == "__main__": unittest.main()
