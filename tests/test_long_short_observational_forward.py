import sqlite3
import tempfile
import unittest
from datetime import datetime,timezone,timedelta
from long_short_observational_forward import resolve_observational_forward

class ObservationalForwardTests(unittest.TestCase):
    def test_direction_and_idempotence(self):
        now=datetime(2026,10,10,16,0,tzinfo=timezone.utc)
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            with sqlite3.connect(f.name) as con:
                con.execute("""CREATE TABLE events(id INTEGER PRIMARY KEY,symbol TEXT,
                    direction TEXT,stage_to TEXT,event_time_utc TEXT,price REAL,payload_json TEXT)""")
                t=(now-timedelta(hours=4)).isoformat()
                con.executemany("INSERT INTO events VALUES(?,?,?,?,?,?,?)",[
                    (1,"AAAUSDT","LONG","APPROACHING",t,100.0,'{"_live_price_source":"BINANCE_SPOT"}'),
                    (2,"BBBUSDT","SHORT","CLOSE_CONFIRMED",t,100.0,'{"_live_price_source":"BINANCE_SPOT"}')])
            def fetch(sym,start,horizons,venue):
                base=datetime.fromisoformat(start)
                price=101 if sym=="AAAUSDT" else 99
                return {h:((base+timedelta(minutes=h)).isoformat(),price) for h in horizons}
            first=resolve_observational_forward(f.name,fetch,now)
            self.assertEqual(first["inserted"],8)
            second=resolve_observational_forward(f.name,fetch,now)
            self.assertEqual(second["inserted"],0)
            with sqlite3.connect(f.name) as con:
                rows=con.execute("SELECT signed_return_pct FROM observational_forward").fetchall()
                self.assertEqual(len(rows),8)
                self.assertTrue(all(r[0]>0 for r in rows))

    def test_rollout_dedup_does_not_change_raw_counts(self):
        now=datetime(2026,10,10,20,0,tzinfo=timezone.utc)
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            with sqlite3.connect(f.name) as con:
                con.execute("""CREATE TABLE events(id INTEGER PRIMARY KEY,symbol TEXT,
                    direction TEXT,stage_to TEXT,event_time_utc TEXT,price REAL,payload_json TEXT)""")
                for i in (1,2):
                    con.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?)",
                        (i,"AAAUSDT","LONG","APPROACHING",
                         (now-timedelta(hours=4,minutes=i)).isoformat(),100.0,
                         '{"_live_price_source":"BINANCE_SPOT"}'))
            def fetch(sym,start,horizons,venue):
                base=datetime.fromisoformat(start)
                return {h:((base+timedelta(minutes=h)).isoformat(),101.0) for h in horizons}
            result=resolve_observational_forward(f.name,fetch,now)
            self.assertEqual(result["inserted"],8)
            with sqlite3.connect(f.name) as con:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM observational_forward").fetchone()[0],8)

    def test_missing_prices_remain_pending(self):
        now=datetime(2026,10,10,16,0,tzinfo=timezone.utc)
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            with sqlite3.connect(f.name) as con:
                con.execute("""CREATE TABLE events(id INTEGER PRIMARY KEY,symbol TEXT,
                    direction TEXT,stage_to TEXT,event_time_utc TEXT,price REAL,payload_json TEXT)""")
                con.execute("INSERT INTO events VALUES(1,'AAAUSDT','LONG','APPROACHING',?,100,'{\"_live_price_source\":\"BINANCE_SPOT\"}')",
                    ((now-timedelta(hours=4)).isoformat(),))
            r=resolve_observational_forward(f.name,lambda *args:{},now)
            self.assertEqual(r["inserted"],0)
            self.assertEqual(r["summary"],[])

if __name__=="__main__": unittest.main()
