import unittest
from unittest.mock import patch
import binance_futures_feed_probe as probe

class FuturesFeedProbeTests(unittest.TestCase):
    def test_fresh_native_rest_candle(self):
        t=2_000_000_000_000
        class R:
            status_code=200
            def json(self):
                return [[t-240000,"1","2","1","1.5","10",t-180001,"1",1,"1","1"],
                        [t-180000,"1","2","1","1.6","10",t-120001,"1",1,"1","1"],
                        [t-60000,"1","2","1","1.7","10",t-1,"1",1,"1","1"]]
        with patch.object(probe,"now_ms",return_value=t),patch.object(probe.requests,"get",return_value=R()):
            x=probe.rest_check(probe.HOSTS[0])
        self.assertTrue(x["usable"])
        self.assertEqual(x["close_price"],1.7)
    def test_http_451_never_becomes_usable(self):
        class R:
            status_code=451
        with patch.object(probe.requests,"get",return_value=R()):
            x=probe.rest_check(probe.HOSTS[0])
        self.assertFalse(x["usable"])
        self.assertEqual(x["error"],"HTTP_451")
