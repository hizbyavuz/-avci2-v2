"""Check that SQLite serializes competing 451 notice transactions."""
import os
import sqlite3
import tempfile
import threading
import unittest


class NoticeLockTests(unittest.TestCase):
    def test_second_writer_waits_for_first(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "notices.db")
            first = sqlite3.connect(path, timeout=2)
            first.execute("BEGIN IMMEDIATE")
            second_entered = threading.Event()
            second_done = threading.Event()
            failures = []

            def second_writer():
                try:
                    with sqlite3.connect(path, timeout=2) as conn:
                        second_entered.set()
                        conn.execute("BEGIN IMMEDIATE")
                        conn.execute("CREATE TABLE IF NOT EXISTS notice_test (id INTEGER)")
                        conn.commit()
                except Exception as exc:
                    failures.append(str(exc))
                finally:
                    second_done.set()

            thread = threading.Thread(target=second_writer)
            thread.start()
            self.assertTrue(second_entered.wait(1))
            self.assertFalse(second_done.wait(0.05))
            first.commit()
            first.close()
            self.assertTrue(second_done.wait(2))
            thread.join()
            self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
