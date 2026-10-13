"""Live-pool SQLite: readers never block the writer; delivered events are not lost."""
import io
import os
import sqlite3
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import long_short_live_pool as lp


class LiveDbLockingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "live.db")
        with sqlite3.connect(self.db) as con:
            con.execute("CREATE TABLE events(id INTEGER PRIMARY KEY, symbol TEXT)")

    def test_wal_enabled(self):
        self.assertEqual(lp.enable_wal(self.db), "wal")

    def test_long_reader_does_not_block_writer_in_wal(self):
        lp.enable_wal(self.db)
        reader = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM events").fetchone()  # like PRAGMA quick_check / audits
        writer = sqlite3.connect(self.db, timeout=0.2)
        writer.execute("INSERT INTO events(symbol) VALUES('ZECUSDT')")
        writer.commit()  # rollback-journal mode raised 'database is locked' here
        writer.close(); reader.rollback(); reader.close()

    def test_delivered_event_survives_transient_writer_lock(self):
        lp.enable_wal(self.db)
        blocker = sqlite3.connect(self.db, check_same_thread=False)
        blocker.execute("BEGIN IMMEDIATE")
        threading.Timer(0.5, blocker.rollback).start()
        con = sqlite3.connect(self.db, timeout=0.1)
        with patch.object(lp.time, "sleep", lambda s: threading.Event().wait(0.3)), \
             redirect_stdout(io.StringIO()) as out:
            lp._commit_with_lock_retry(con, "INSERT INTO events(symbol) VALUES(?)", ("ZECUSDT",))
        self.assertIn("LIVE_DB_LOCK_RETRY", out.getvalue())
        self.assertEqual(con.execute("SELECT symbol FROM events").fetchall(), [("ZECUSDT",)])
        con.close(); blocker.close()


if __name__ == "__main__":
    unittest.main()
