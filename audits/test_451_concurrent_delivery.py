"""Two concurrent workers must not send the same notice twice."""
import os
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timezone


class ConcurrentDeliveryTests(unittest.TestCase):
    def test_one_delivery_for_same_symbol_and_direction(self):
        with tempfile.TemporaryDirectory() as folder:
            db = os.path.join(folder, "notice.db")
            sent = []
            errors = []
            start = threading.Barrier(2)

            def worker():
                try:
                    start.wait(timeout=3)
                    with sqlite3.connect(db, timeout=5) as con:
                        con.execute("BEGIN IMMEDIATE")
                        con.execute("CREATE TABLE IF NOT EXISTS notices (symbol TEXT, direction TEXT, sent_at TEXT, PRIMARY KEY(symbol,direction))")
                        existing = con.execute("SELECT sent_at FROM notices WHERE symbol=? AND direction=?", ("ETHUSDT", "LONG")).fetchone()
                        if existing is None:
                            sent.append(("ETHUSDT", "LONG"))
                            con.execute("INSERT INTO notices VALUES (?,?,?)", ("ETHUSDT", "LONG", datetime.now(timezone.utc).isoformat()))
                        con.commit()
                except Exception as exc:
                    errors.append(str(exc))

            threads = [threading.Thread(target=worker) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=6)
            self.assertFalse(any(t.is_alive() for t in threads))
            self.assertEqual(errors, [])
            self.assertEqual(sent, [("ETHUSDT", "LONG")])


if __name__ == "__main__":
    unittest.main()
