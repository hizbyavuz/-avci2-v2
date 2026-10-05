#!/usr/bin/env python3
import unittest
from unittest.mock import patch

import long_short_data_router as r


class RouterTests(unittest.TestCase):
    def test_bybit_full_bundle(self):
        def fake(path, params=None):
            if path.endswith("/tickers"):
                return {"result":{"list":[{
                    "markPrice":"100.1","indexPrice":"100.0","fundingRate":"0.0001"
                }]}, "server_time_ms": 1}
            if path.endswith("/open-interest"):
                rows=[{"openInterest":str(100+i),"timestamp":str(1000+i)} for i in range(13)]
                return {"result":{"list":rows}, "server_time_ms": 1}
            if path.endswith("/account-ratio"):
                return {"result":{"list":[{"buyRatio":"0.55","sellRatio":"0.45","timestamp":"1"}]}, "server_time_ms": 1}
            if path.endswith("/recent-trade"):
                return {"result":{"list":[
                    {"price":"100","size":"2","side":"Buy"},
                    {"price":"100","size":"1","side":"Sell"},
                ]}, "server_time_ms": 1}
            if path.endswith("/orderbook"):
                return {"result":{"b":[["100","3"]],"a":[["101","1"]]}, "server_time_ms": 1}
            raise AssertionError(path)

        with patch.object(r, "_bybit", side_effect=fake):
            x=r.bybit_derivatives("BTCUSDT")
        self.assertEqual(x["quality"],"FULL")
        self.assertEqual(x["coverage"],1.0)
        self.assertGreater(x["oi_change_1h"],0)
        self.assertAlmostEqual(x["funding_pct"],0.01,places=6)
        self.assertGreater(x["taker_ratio"],1.0)
        self.assertGreater(x["long_short_ratio"],1.0)
        self.assertGreater(x["depth_imbalance"],0)

    def test_fusion_uses_okx_only_for_missing_field(self):
        by={
            "provider":"BYBIT_LINEAR","symbol":"BTCUSDT","errors":[],
            "oi_change_1h":2.0,"oi_now":10.0,"funding_pct":0.01,
            "taker_ratio":1.2,"long_short_ratio":None,"depth_imbalance":0.1,
            "mark_price":100.0,"index_price":100.0,"basis_pct":0.0,
            "field_source":{
                "oi_change_1h":"BYBIT_LINEAR","oi_now":"BYBIT_LINEAR",
                "funding_pct":"BYBIT_LINEAR","taker_ratio":"BYBIT_LINEAR",
                "depth_imbalance":"BYBIT_LINEAR"
            }
        }
        ok={
            "provider":"OKX_SWAP","symbol":"BTCUSDT","errors":[],
            "oi_change_1h":None,"oi_now":20.0,"funding_pct":0.02,
            "taker_ratio":0.9,"long_short_ratio":1.1,"depth_imbalance":-0.1,
            "mark_price":100.0,"index_price":100.0,"basis_pct":0.0,
            "field_source":{"long_short_ratio":"OKX_SWAP"}
        }
        with patch.object(r,"bybit_derivatives",return_value=by), patch.object(r,"okx_derivatives",return_value=ok):
            x=r.multi_venue_derivatives("BTCUSDT")
        self.assertEqual(x["long_short_ratio"],1.1)
        self.assertEqual(x["field_source"]["long_short_ratio"],"OKX_SWAP")
        self.assertEqual(x["quality"],"FULL")
        self.assertEqual(x["coverage"],1.0)


if __name__=="__main__":
    unittest.main()
