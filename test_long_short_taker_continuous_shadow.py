import os
import sqlite3
import tempfile
import unittest
from long_short_taker_continuous_shadow import initialize,record_minute,window

class TakerMinuteTests(unittest.TestCase):
    def test_complete_five_closed_minutes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=os.path.join(tmp,"taker.db")
            now=600000
            with sqlite3.connect(path) as c:
                initialize(c)
                for minute in range(300000,600000,60000):
                    record_minute(c,"BTCUSDT",minute,100,50,2,minute+1000,minute+2000)
                c.execute("INSERT INTO taker_collector_health VALUES(?,?,?,?,?)",
                          (now,"ROTATE",'["BTCUSDT"]',0,'{"connected_at_ms":300000}'))
                c.commit()
            result=window(path,"BTCUSDT",now)
            self.assertTrue(result["ready"])
            self.assertEqual(result["taker_ratio"],2.0)

    def test_active_session_heartbeat_allows_closed_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=os.path.join(tmp,"taker.db")
            now=600000
            with sqlite3.connect(path) as conn:
                initialize(conn)
                for minute in range(300000,600000,60000):
                    record_minute(conn,"BTCUSDT",minute,100,50,2,minute+1000,minute+2000)
                conn.execute("INSERT INTO taker_collector_health VALUES(?,?,?,?,?)",
                    (now,"CONNECTED",'["BTCUSDT"]',0,'{"connected_at_ms":299000}'))
                conn.commit()
            self.assertTrue(window(path,"BTCUSDT",now)["ready"])
            self.assertFalse(window(path,"BTCUSDT",now+31000)["ready"])

    def test_reconnect_does_not_borrow_previous_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=os.path.join(tmp,"taker.db")
            now=600000
            with sqlite3.connect(path) as conn:
                initialize(conn)
                for minute in range(300000,600000,60000):
                    record_minute(conn,"BTCUSDT",minute,100,50,2,minute+1000,minute+2000)
                conn.execute("INSERT INTO taker_collector_health VALUES(?,?,?,?,?)",
                    (now,"CONNECTED",'["BTCUSDT"]',0,'{"connected_at_ms":480000}'))
                conn.commit()
            self.assertFalse(window(path,"BTCUSDT",now)["ready"])

    def test_missing_minute_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=os.path.join(tmp,"taker.db")
            with sqlite3.connect(path) as c:
                initialize(c)
                record_minute(c,"BTCUSDT",300000,100,50,2,301000,302000)
                c.commit()
            self.assertFalse(window(path,"BTCUSDT",600000)["ready"])

if __name__=="__main__":
    unittest.main()
