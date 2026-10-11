import os
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from long_short_451_outcomes import record_observations
from long_short_451_price_source import closed_quote


class NonfiniteTests(unittest.TestCase):
    def test_closed_quote_rejects_nan_and_infinity(self):
        for value in ("nan", "inf", "-inf"):
            with self.subTest(value=value):
                def fetch(*args):
                    return {"raw": [[0,0,0,0,value,0,1791712800000]]}
                self.assertIsNone(closed_quote("ETHUSDT", "2026-10-11T10:00:00+00:00", fetch))

    def test_observations_reject_nonfinite_entry(self):
        with tempfile.TemporaryDirectory() as folder:
            db = os.path.join(folder, "test.db")
            for value in (float("nan"), float("inf"), float("-inf")):
                a = SimpleNamespace(symbol="ETHUSDT",price=value,
                    payload={"direction_engine":{"direction":"LONG","observation_only":True,"eligible":False}})
                record_observations(db, "2026-10-11T10:00:00+00:00", [a])
            with sqlite3.connect(db) as con:
                count = con.execute("SELECT count(*) FROM watch_451_observations").fetchone()[0]
            self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
