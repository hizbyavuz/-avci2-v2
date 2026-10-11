"""Offline smoke checks for GitHub deployment source. Run: python -m unittest discover -s gate_trader/tests -v"""
import unittest
from unittest.mock import patch
from gate_trader import config, gate_api, engine, ledger, messages, outcome, series, telegram
from gate_trader.gates import Decision, fmt, trend

class SmokeTests(unittest.TestCase):
    def test_all_runtime_modules_import(self):
        self.assertTrue(callable(engine.scan))
        self.assertTrue(callable(engine.track))
        self.assertTrue(callable(telegram.send))
    def test_closed_candles_only(self):
        s=series.Series("15m",t=[0,900,1800],o=[1]*3,h=[2]*3,l=[0]*3,c=[1]*3,qv=[10]*3)
        self.assertEqual(len(s.drop_open_bar(2400)),2)
    def test_same_bar_stop_first(self):
        r=outcome.simulate("LONG",99,102,[0],[103],[98],[101],300,10000)
        self.assertEqual(r.status,"SL")
    def test_short_pnl_direction(self):
        self.assertGreater(outcome.pnl_usd("SHORT",100,95,2),0)
        self.assertLess(outcome.pnl_usd("SHORT",100,105,2),0)
    def test_ram_ledger(self):
        with ledger.connect() as db:
            self.assertIsNotNone(ledger.stats(db))
    def test_market_data_parser(self):
        data=[{"t":"0","o":"1","h":"2","l":"0.5","c":"1.5","sum":"100"},
              {"t":"900","o":"1.5","h":"2","l":"1","c":"1.8","sum":"150"}]
        s=gate_api._parse(data,"15m")
        self.assertEqual(len(s),1)  # t=0 deliberately discarded
        self.assertEqual(s.c[-1],1.8)
    def test_no_auto_orders(self):
        self.assertFalse(hasattr(gate_api,"place_order"))
        self.assertFalse(hasattr(engine,"place_order"))

if __name__=="__main__":
    unittest.main()
