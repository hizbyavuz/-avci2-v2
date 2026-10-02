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
    def test_no_order_endpoint(self):
        # Safety invariant: module must not contain Binance order placement endpoints.
        import inspect
        src=inspect.getsource(m)
        self.assertNotIn("/fapi/v1/order", src)
        self.assertNotIn("/fapi/v1/batchOrders", src)

if __name__=="__main__":
    unittest.main()
