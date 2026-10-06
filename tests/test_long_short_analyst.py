import unittest
import long_short_analyst as m

class TestLongShortAnalyst(unittest.TestCase):
    def test_pct(self):
        self.assertAlmostEqual(m.pct(100,110),10.0)
    def test_ema(self):
        self.assertGreater(m.ema([1,2,3,4,5],3),3)
    def test_rsi_bounds(self):
        x=m.rsi(list(range(1,30)))
        self.assertTrue(0 <= x <= 100)
    def test_status_text(self):
        self.assertIn("LONG", m.turkish_status("LONG"))
    def test_activity_shortlist_does_not_reserve_slots_for_volume_leaders(self):
        rows=[
            {"symbol":"BTCUSDT","rank":8.0,"day_change":1.0,"quote_volume":10_000_000_000},
            {"symbol":"ETHUSDT","rank":7.0,"day_change":1.5,"quote_volume":8_000_000_000},
            {"symbol":"RLCUSDT","rank":30.0,"day_change":60.0,"quote_volume":1_200_000_000},
            {"symbol":"CAPUSDT","rank":24.0,"day_change":38.0,"quote_volume":100_000_000},
            {"symbol":"NMRUSDT","rank":20.0,"day_change":36.0,"quote_volume":110_000_000},
        ]
        got=m.select_deep_shortlist(rows,3)
        self.assertEqual([x["symbol"] for x in got],["RLCUSDT","CAPUSDT","NMRUSDT"])

    def test_no_order_endpoint(self):
        # Safety invariant: module must not contain Binance order placement endpoints.
        import inspect
        src=inspect.getsource(m)
        self.assertNotIn("/fapi/v1/order", src)
        self.assertNotIn("/fapi/v1/batchOrders", src)

if __name__=="__main__":
    unittest.main()
