import unittest
from binance_native_um_market_watch import parse_tickers,add,metrics
class TestBinanceNativeUMMarket(unittest.TestCase):
    def test_native_usdm_filter_and_freshness(self):
        t=2000000000000
        rows=[{"e":"24hrTicker","st":1,"s":"BTCUSDT","E":t-1000,"c":"100","q":"100"},
              {"e":"24hrTicker","st":2,"s":"BTCUSDT","E":t-1000,"c":"100","q":"100"},
              {"e":"24hrTicker","s":"ETHUSDT","E":t-1000,"c":"10","q":"10"},
              {"e":"24hrTicker","st":1,"s":"ADAUSDT","E":t-100000,"c":"10","q":"10"}]
        self.assertEqual([x[0] for x in parse_tickers(rows,t)],["BTCUSDT"])
    def test_directional_change_only_with_history(self):
        t=2000000000000
        h={}
        add(h,"TESTUSDT",t-300000,100)
        add(h,"TESTUSDT",t,94)
        z=metrics(h,{"TESTUSDT":(t,94,10000)},t)
        self.assertEqual(z["5"]["down5"],1)
        self.assertEqual(z["5"]["strongest_down"][0]["actual_seconds"],300)
        self.assertEqual(z["15"]["comparable"],0)
        self.assertEqual(z["15"]["missing_baseline"],1)
    def test_out_of_order(self):
        h={}
        add(h,"BTCUSDT",200,12)
        add(h,"BTCUSDT",150,1)
        self.assertEqual(h["BTCUSDT"],[[200,12]])
