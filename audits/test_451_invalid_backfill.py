"""Non-finite quotes must not become research outcome labels."""
import os
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from long_short_451_outcomes import record_observations, backfill_pending


class InvalidBackfillTests(unittest.TestCase):
    def test_nan_quote_does_not_create_label(self):
        with tempfile.TemporaryDirectory() as folder:
            db = os.path.join(folder, "outcomes.db")
            analysis = SimpleNamespace(symbol="ETHUSDT", price=100.0,
                payload={"direction_engine": {"direction": "LONG", "observation_only": True, "eligible": False}})
            record_observations(db, "2026-10-11T10:00:00+00:00", [analysis])
            count = backfill_pending(db, "2026-10-11T10:15:00+00:00",
                lambda symbol: (float("nan"), "2026-10-11T10:15:00+00:00"))
            with sqlite3.connect(db) as con:
                labels = con.execute("SELECT COUNT(*) FROM watch_451_labels").fetchone()[0]
            self.assertEqual(count, 0)
            self.assertEqual(labels, 0)


if __name__ == "__main__":
    unittest.main()
