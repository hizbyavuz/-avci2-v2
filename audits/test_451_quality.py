import os
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from long_short_451_outcomes import record_observations

class QualityTests(unittest.TestCase):
    def test_unknown_venue_is_provisional(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "quality.db")
            a = SimpleNamespace(symbol="ETHUSDT", price=100.0,
                payload={"direction_engine":{"direction":"LONG","observation_only":True,"eligible":False}})
            record_observations(path,"2026-10-11T10:00:00+00:00",[a])
            with sqlite3.connect(path) as con:
                row = con.execute("SELECT status,reason FROM watch_451_quality").fetchone()
            self.assertEqual(row,("PROVISIONAL","ENTRY_EXIT_VENUE_NOT_VERIFIED"))

if __name__ == "__main__":
    unittest.main()
