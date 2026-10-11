import os
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace

from long_short_451_outcomes import record_observations, pending_symbols, backfill_pending


def analysis(symbol="ETHUSDT", price=100, direction="LONG", observed=True, eligible=False):
    return SimpleNamespace(symbol=symbol, price=price, payload={
        "direction_engine": {"direction": direction, "observation_only": observed, "eligible": eligible}
    })


class OutcomeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "watch.db")

    def tearDown(self):
        self.tmp.cleanup()

    def rows(self, table):
        with sqlite3.connect(self.db) as con:
            return con.execute("SELECT * FROM " + table).fetchall()

    def test_long_directional_returns_and_horizons(self):
        record_observations(self.db, "2026-10-11T10:00:00+00:00", [analysis()])
        record_observations(self.db, "2026-10-11T10:15:00+00:00", [analysis(price=105, observed=False)])
        record_observations(self.db, "2026-10-11T11:00:00+00:00", [analysis(price=110, observed=False)])
        record_observations(self.db, "2026-10-11T14:00:00+00:00", [analysis(price=120, observed=False)])
        labels = self.rows("watch_451_labels")
        self.assertEqual([x[3] for x in labels], [15, 60, 240])
        self.assertEqual([round(x[6], 2) for x in labels], [5, 10, 20])

    def test_short_positive_on_falling_price(self):
        record_observations(self.db, "2026-10-11T10:00:00+00:00", [analysis(direction="SHORT")])
        record_observations(self.db, "2026-10-11T10:15:00+00:00", [analysis(price=95, observed=False)])
        self.assertEqual(round(self.rows("watch_451_labels")[0][6], 2), 5)

    def test_missing_future_price_remains_unresolved(self):
        record_observations(self.db, "2026-10-11T10:00:00+00:00", [analysis()])
        record_observations(self.db, "2026-10-11T14:00:00+00:00", [])
        self.assertEqual(self.rows("watch_451_labels"), [])

    def test_stale_snapshot_cannot_fake_15_minute_outcome(self):
        record_observations(self.db, "2026-10-11T10:00:00+00:00", [analysis()])
        record_observations(self.db, "2026-10-11T11:00:00+00:00", [analysis(price=120, observed=False)])
        labels = self.rows("watch_451_labels")
        self.assertEqual([row[3] for row in labels], [60])

    def test_trade_eligible_never_observed(self):
        record_observations(self.db, "2026-10-11T10:00:00+00:00", [analysis(eligible=True)])
        self.assertEqual(self.rows("watch_451_observations"), [])

    def test_no_pre_signal_leakage(self):
        record_observations(self.db, "2026-10-11T10:00:00+00:00", [analysis()])
        record_observations(self.db, "2026-10-11T09:45:00+00:00", [analysis(price=90, observed=False)])
        self.assertEqual(self.rows("watch_451_labels"), [])


    def test_pending_symbol_backfill_after_deep_scan_omission(self):
        record_observations(self.db, "2026-10-11T10:00:00+00:00", [analysis()])
        self.assertEqual(pending_symbols(self.db, "2026-10-11T10:30:00+00:00"), ["ETHUSDT"])
        backfill_pending(self.db, "2026-10-11T10:30:00+00:00",
                         lambda symbol: (106, "2026-10-11T10:30:00+00:00"))
        labels = self.rows("watch_451_labels")
        self.assertEqual(len(labels), 1)
        self.assertEqual(labels[0][3], 15)
        self.assertEqual(round(labels[0][6], 2), 6)

    def test_future_dated_quote_is_rejected(self):
        record_observations(self.db, "2026-10-11T10:00:00+00:00", [analysis()])
        backfill_pending(self.db, "2026-10-11T10:30:00+00:00",
                         lambda symbol: (106, "2026-10-11T10:35:00+00:00"))
        self.assertEqual(self.rows("watch_451_labels"), [])

    def test_expired_pending_symbols_not_fetched(self):
        record_observations(self.db, "2026-10-11T10:00:00+00:00", [analysis()])
        self.assertEqual(pending_symbols(self.db, "2026-10-12T10:00:00+00:00"), [])


if __name__ == "__main__":
    unittest.main()
