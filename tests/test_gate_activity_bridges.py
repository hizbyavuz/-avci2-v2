import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from gate_activity_bridge import review as review_activity
from gate_cross_venue_bridge import review as review_cross
from gate_early_observer import record_scan
from gate_spot_observer import save_snapshot

ADDRESS = "0x" + "a" * 40


def item(price=1.0, liquidity=50000, volume5=1000, buyers=4):
    return {"network_id":"eth","token_contract":ADDRESS,"pool":"pool",
        "price_usd":price,"liquidity":liquidity,"volume_5m":volume5,
        "volume_1h":10000,"volume_24h":100000,
        "buys_5m":20,"sells_5m":10,"buys_24h":200,"sells_24h":100,
        "change_24h":5,"unique_buyers_5m":buyers,"unique_buyers_1h":20}


class GateActivityBridgeTest(unittest.TestCase):
    def test_buyer_and_liquidity_engines_still_fail_closed_on_security(self):
        with tempfile.TemporaryDirectory() as directory:
            path=str(Path(directory)/"state.db")
            now=int(time.time())
            for i in range(4):
                record_scan(path, f"p{i}", [item()], now_ts=now-2400+i*600)
            current=item(price=1.01, liquidity=65000, volume5=2200, buyers=20)
            record_scan(path, "current", [current], now_ts=now)
            with patch("gate_activity_bridge.security_decision",
                       return_value="security blocked"):
                result=review_activity(path,"current",[current],lambda _:None,
                    (lambda _:{"risk":False},lambda _:{"risk":False}),now=now)
            self.assertGreaterEqual(result["qualified"],1)
            self.assertEqual(result["pending"],0)

    def test_cross_venue_requires_exact_contract_and_same_security_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            path=str(Path(directory)/"state.db")
            now=int(time.time())
            for i in range(4):
                record_scan(path, f"p{i}", [item()], now_ts=now-2400+i*600)
            current=item(price=1.01, volume5=2200, buyers=12)
            record_scan(path, "current", [current], now_ts=now)
            save_snapshot(path, "spot", [
                ("COIN_USDT","COIN","Coin",1.0,500000,5)
            ], [("COIN_USDT","eth",ADDRESS)],
                quality=[("COIN_USDT",now-40*86400,.999,1.001)])
            with patch("gate_cross_venue_bridge.security_decision",
                       return_value="security blocked"):
                result=review_cross(path,"current",[current],lambda _:None,
                    (lambda _:{"risk":False},lambda _:{"risk":False}),now=now)
            self.assertEqual(result["qualified"],1)
            self.assertEqual(result["pending"],0)


if __name__ == "__main__":
    unittest.main()
