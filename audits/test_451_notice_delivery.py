"""Offline simulation of two workers selecting the same 451 notice."""
import os
import sqlite3
import tempfile
import threading
import unittest
from long_short_451_watch_digest import format_watch_digest
from types import SimpleNamespace


class NoticeDeliveryTests(unittest.TestCase):
    def test_concurrent_selection_sends_only_once(self):
        with tempfile.TemporaryDirectory() as directory:
            db = os.path.join(directory, "watch.db")
            with sqlite3.connect(db) as conn:
                conn.execute("CREATE TABLE notices (symbol TEXT, direction TEXT, PRIMARY KEY(symbol,direction))")
            sent = []
            errors = []
            barrier = threading.Barrier(2)
            candidate = SimpleNamespace(symbol="ETHUSDT", price=100.0,
                payload={"direction_engine":{"direction":"LONG","observation_only":True,"eligible":False}})

            def worker():
                try:
                    barrier.wait(timeout=2)
                    with sqlite3.connect(db, timeout=3) as conn:
                        conn.execute("BEGIN IMMEDIATE")
                        exists = conn.execute("SELECT 1 FROM notices WHERE symbol=? AND direction=?",
                            ("ETHUSDT", "LONG")).fetchone()
                        if not exists:
                            message = format_watch_digest([candidate])
                            if message:
                                sent.append(message)
                                conn.execute("INSERT INTO notices VALUES (?,?)", ("ETHUSDT", "LONG"))
                                conn.commit()
                except Exception as exc:
                    errors.append(str(exc))

            threads = [threading.Thread(target=worker) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=4)
            self.assertEqual(errors, [])
            self.assertEqual(len(sent), 1)
            self.assertIn("İşlem teyidi DEĞİL", sent[0])


if __name__ == "__main__":
    unittest.main()
