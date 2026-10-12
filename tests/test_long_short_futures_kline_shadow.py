import sqlite3
import tempfile
import unittest

from long_short_futures_kline_shadow import initialize, parse_closed_kline, store


class FuturesKlineShadowTests(unittest.TestCase):
    def test_only_closed_futures_candles(self):
        packet = {"stream":"btcusdt@kline_5m","data":{"e":"kline","s":"BTCUSDT",
            "k":{"i":"5m","t":1000,"T":300999,"o":"100","h":"102",
                 "l":"99","c":"101","v":"25","x":False}}}
        self.assertIsNone(parse_closed_kline(packet))
        packet["data"]["k"]["x"] = True
        row = parse_closed_kline(packet)
        self.assertEqual(row[:4], ("BTCUSDT","5m",1000,300999))
        self.assertEqual(row[7],101.0)

    def test_upsert_is_idempotent(self):
        packet={"e":"kline","s":"ETHUSDT","k":{"i":"1m","t":1000,"T":60999,
            "o":"10","h":"12","l":"9","c":"11","v":"20","x":True}}
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            con=initialize(tmp.name)
            row=parse_closed_kline(packet)
            store(con,row)
            store(con,row)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM futures_closed_klines").fetchone()[0],1)
            con.close()


if __name__=="__main__":
    unittest.main()
