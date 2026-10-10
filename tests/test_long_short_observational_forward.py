import io
import json
from contextlib import redirect_stdout
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

    def test_since_1409_dedup_report(self):
        now=datetime(2026,10,10,20,0,tzinfo=timezone.utc)
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            with sqlite3.connect(f.name) as con:
                con.execute("""CREATE TABLE events(id INTEGER PRIMARY KEY,symbol TEXT,
                    direction TEXT,stage_to TEXT,event_time_utc TEXT,price REAL,payload_json TEXT)""")
                for i,minute in enumerate((8,10,11),1):
                    con.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?)",
                        (i,"AAAUSDT","LONG","APPROACHING",
                         datetime(2026,10,10,11,minute,tzinfo=timezone.utc).isoformat(),
                         100.0,'{"_live_price_source":"BINANCE_SPOT"}'))
            def fetch(sym,start,horizons,venue):
                base=datetime.fromisoformat(start)
                return {h:((base+timedelta(minutes=h)).isoformat(),101.0) for h in horizons}
            output=io.StringIO()
            with redirect_stdout(output):
                resolve_observational_forward(f.name,fetch,now)
            line=next(x for x in output.getvalue().splitlines()
                      if x.startswith("OBS_FORWARD_SINCE_1409_DEDUP "))
            report=json.loads(line.split(" ",1)[1])
            diagnostic=json.loads(next(x for x in output.getvalue().splitlines()
                if x.startswith("OBS_FORWARD_SINCE_1409_DIAGNOSTIC ")).split(" ",1)[1])
            self.assertEqual(diagnostic["raw_events"],2)
            self.assertEqual(diagnostic["eligible_events"],2)
            self.assertEqual(diagnostic["observed_events"],2)
            self.assertEqual(diagnostic["observed_horizons"],8)
            self.assertEqual(report["start_turkey"],"2026-10-10 14:09")
            self.assertEqual(len(report["summary"]),4)
            self.assertTrue(all(row["dedup_events"]==1 for row in report["summary"]))

    def test_recent_cohort_priority_with_small_limit(self):
        now=datetime(2026,10,10,20,0,tzinfo=timezone.utc)
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            with sqlite3.connect(f.name) as con:
                con.execute("""CREATE TABLE events(id INTEGER PRIMARY KEY,symbol TEXT,
                    direction TEXT,stage_to TEXT,event_time_utc TEXT,price REAL,payload_json TEXT)""")
                for i,t in ((1,"2026-10-09T11:10:00+00:00"),
                            (2,"2026-10-10T11:10:00+00:00")):
                    con.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?)",
                        (i,"AAAUSDT","LONG","APPROACHING",t,100.0,
                         '{"_live_price_source":"BINANCE_SPOT"}'))
            def fetch(sym,start,horizons,venue):
                base=datetime.fromisoformat(start)
                return {h:((base+timedelta(minutes=h)).isoformat(),101.0) for h in horizons}
            resolve_observational_forward(f.name,fetch,now,limit=1)
            with sqlite3.connect(f.name) as con:
                self.assertEqual(con.execute(
                    "SELECT DISTINCT event_id FROM observational_forward").fetchall(),[(2,)])
            resolve_observational_forward(f.name,fetch,now,limit=1)
            with sqlite3.connect(f.name) as con:
                self.assertEqual(set(x[0] for x in con.execute(
                    "SELECT DISTINCT event_id FROM observational_forward")),{1,2})

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
