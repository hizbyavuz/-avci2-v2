import unittest
from long_short_v1_delivery_audit import measure

SOURCE = 'BINANCE_UM_FUTURES_WEBSOCKET'


class DeliveryAuditTests(unittest.TestCase):
    def rows(self):
        return [(t,t+59999,102,98,101,SOURCE) for t in range(0,1860000,60000)]

    def test_no_partial_minute_or_future_endpoint(self):
        rows = self.rows()
        rows[0] = (0,59999,999,1,100,SOURCE)
        rows[15] = (900000,959999,999,1,900,SOURCE)
        m = measure(100,30000,'LONG',rows,15)
        self.assertEqual(m['endpoint_ms'],899999)
        self.assertAlmostEqual(m['directional_change_pct'],1)
        self.assertAlmostEqual(m['favorable_excursion_pct'],2)
        self.assertAlmostEqual(m['adverse_excursion_pct'],2)

    def test_short_sign_and_30m(self):
        m = measure(100,30000,'SHORT',self.rows(),30)
        self.assertEqual(m['endpoint_ms'],1799999)
        self.assertAlmostEqual(m['directional_change_pct'],-1)

    def test_gaps_bad_sources_and_missing_price_not_losses(self):
        rows = self.rows()
        self.assertEqual(measure(100,30000,'LONG',rows[2:],15)['state'],'MISSING_NATIVE_PATH')
        rows[2] = (*rows[2][:-1], 'BINANCE_SPOT')
        self.assertEqual(measure(100,30000,'LONG',rows,15)['state'],'INVALID_NATIVE_PATH')
        self.assertEqual(measure(None,30000,'LONG',rows,15)['state'],'MISSING_ACK_PRICE')
