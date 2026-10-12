import unittest
from long_short_futures_universe_worker import parse_ticker

class FuturesUniverseTests(unittest.TestCase):
    def test_accepts_fresh_futures_ticker(self):
        now=1_800_000_000_000
        rows=parse_ticker({"data":[{"e":"24hrTicker","E":now-1000,
          "s":"BTCUSDT","q":"12000000","c":"60000","P":"2.5"}]},now)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0][0],"BTCUSDT")
        self.assertEqual(rows[0][-1],"BINANCE_UM_FUTURES_WEBSOCKET")

    def test_rejects_stale_or_invalid(self):
        now=1_800_000_000_000
        rows=parse_ticker({"data":[
          {"e":"24hrTicker","E":now-200000,"s":"BTCUSDT","q":"100","c":"1","P":"1"},
          {"e":"24hrTicker","E":now,"s":"BTCUSDT","q":"100","c":"0","P":"1"},
          {"e":"24hrTicker","E":now,"s":"BTCUSDC","q":"100","c":"1","P":"1"},
          {"e":"other","E":now,"s":"ETHUSDT","q":"100","c":"1","P":"1"}
        ]},now)
        self.assertEqual(rows,[])

if __name__=="__main__":
    unittest.main()
