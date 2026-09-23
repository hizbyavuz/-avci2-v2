import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from gate_spot_observer import save_snapshot
from gate_spot_watch import round_trip_loss, run, shortlist


ADDRESS = "0x" + "a" * 40


class GateSpotWatchTest(unittest.TestCase):
    def test_round_trip_requires_both_sides_of_book(self):
        self.assertAlmostEqual(round_trip_loss({
            "asks": [["1.01", "2000"]], "bids": [["1", "2000"]]
        }), 1.490099, places=5)
        self.assertIsNone(round_trip_loss({
            "asks": [["1.01", "1"]], "bids": [["1", "2000"]]
        }))

    def test_early_watch_uses_prior_pair_and_book_with_no_telegram(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "data.db")
            now = int(time.time())
            age = now - 40 * 86400
            save_snapshot(path, "earlier", [
                ("GOOD_USDT", "GOOD", "Good", 1, 500000, 4),
            ], [("GOOD_USDT", "eth", ADDRESS)],
                quality=[("GOOD_USDT", age, .999, 1.001)])
            with sqlite3.connect(path) as con:
                con.execute("""UPDATE gate_spot_health SET scan_ts=?
                    WHERE batch_id='earlier'""", (now - 25 * 60,))
            save_snapshot(path, "latest", [
                ("GOOD_USDT", "GOOD", "Good", 1.02, 500000, 6),
                ("UNMAPPED_USDT", "UNMAPPED", "Unmapped", 1.02, 500000, 6),
            ], [("GOOD_USDT", "eth", ADDRESS)], quality=[
                ("GOOD_USDT", age, 1.019, 1.021),
                ("UNMAPPED_USDT", age, 1.019, 1.021),
            ])
            with sqlite3.connect(path) as con:
                self.assertEqual([x["pair"] for x in shortlist(
                    con, now, "latest")], ["GOOD_USDT"])
            result = run(path, lambda pair: {
                "asks": [["1.021", "10000"]],
                "bids": [["1.019", "10000"]],
            })
            self.assertIn("1 derinlik doğrulandı", result)
            with sqlite3.connect(path) as con:
                self.assertEqual(con.execute("""SELECT status FROM gate_spot_watch
                    WHERE pair='GOOD_USDT'""").fetchone()[0], "PAPER_WATCH")
            save_snapshot(path, "later", [
                ("GOOD_USDT", "GOOD", "Good", 1.07, 500000, 10),
            ], [("GOOD_USDT", "eth", ADDRESS)],
                quality=[("GOOD_USDT", age, 1.069, 1.071)])
            with sqlite3.connect(path) as con:
                con.execute("""UPDATE gate_spot_health SET scan_ts=?
                    WHERE batch_id='later'""", (now + 20 * 60,))
            run(path, lambda pair: {
                "asks": [["1.071", "10000"]],
                "bids": [["1.069", "10000"]],
            })
            with sqlite3.connect(path) as con:
                self.assertEqual(con.execute("""SELECT count(*)
                    FROM gate_spot_watch WHERE pair='GOOD_USDT'""").fetchone()[0], 1)
                self.assertAlmostEqual(con.execute("""SELECT sampled_return_pct
                    FROM gate_spot_watch_path""").fetchone()[0], 100 * (1.07 / 1.02 - 1))

    def test_retention_and_prebreakout_paths_do_not_require_fresh_one_percent_jump(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "data.db")
            now = int(time.time())
            age = now - 40 * 86400
            contracts = [
                ("HOLD_USDT", "eth", ADDRESS),
                ("EARLY_USDT", "eth", "0x" + "b" * 40),
            ]
            save_snapshot(path, "older", [
                ("HOLD_USDT", "HOLD", "Hold", 1.00, 500000, 3),
                ("EARLY_USDT", "EARLY", "Early", 1.00, 500000, 3),
            ], contracts, quality=[
                ("HOLD_USDT", age, .999, 1.001),
                ("EARLY_USDT", age, .999, 1.001),
            ])
            with sqlite3.connect(path) as con:
                con.execute("""UPDATE gate_spot_health SET scan_ts=?
                    WHERE batch_id='older'""", (now - 50 * 60,))
            save_snapshot(path, "prior", [
                ("HOLD_USDT", "HOLD", "Hold", 1.03, 500000, 5),
                ("EARLY_USDT", "EARLY", "Early", 1.00, 500000, 4),
            ], contracts, quality=[
                ("HOLD_USDT", age, 1.029, 1.031),
                ("EARLY_USDT", age, .999, 1.001),
            ])
            with sqlite3.connect(path) as con:
                con.execute("""UPDATE gate_spot_health SET scan_ts=?
                    WHERE batch_id='prior'""", (now - 25 * 60,))
            save_snapshot(path, "latest", [
                ("HOLD_USDT", "HOLD", "Hold", 1.025, 500000, 5),
                ("EARLY_USDT", "EARLY", "Early", 1.005, 510000, 5),
            ], contracts, quality=[
                ("HOLD_USDT", age, 1.024, 1.026),
                ("EARLY_USDT", age, 1.004, 1.006),
            ])
            with sqlite3.connect(path) as con:
                rows = shortlist(con, now, "latest")
            paths = {row["pair"]: row["entry_path"] for row in rows}
            self.assertEqual(paths["HOLD_USDT"], "RETENTION")
            self.assertEqual(paths["EARLY_USDT"], "PRE_BREAKOUT")

    def test_unfillable_book_never_becomes_paper_watch(self):
        self.assertIsNone(round_trip_loss({
            "asks": [["1.01", "2000"]], "bids": [["1", "1"]]
        }))


if __name__ == "__main__":
    unittest.main()
