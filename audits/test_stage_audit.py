"""Offline tests for full-universe stage classification and append-safe keys."""
import sqlite3
import unittest

def classify(symbol, prefiltered, selected, failures):
    if symbol in failures:
        return "PREFILTER_ERROR"
    if symbol not in prefiltered:
        return "UNOBSERVED"
    return "DEEP_SELECTED" if symbol in selected else "NOT_SHORTLISTED"

class StageAuditTests(unittest.TestCase):
    def test_all_symbols_classified(self):
        universe = ["AUSDT", "BUSDT", "CUSDT", "DUSDT"]
        result = {s: classify(s, {"AUSDT", "BUSDT"}, {"AUSDT"}, {"CUSDT"}) for s in universe}
        self.assertEqual(result, {"AUSDT":"DEEP_SELECTED", "BUSDT":"NOT_SHORTLISTED",
                                  "CUSDT":"PREFILTER_ERROR", "DUSDT":"UNOBSERVED"})

    def test_primary_key_idempotence(self):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE audit(ts TEXT, symbol TEXT, stage TEXT, PRIMARY KEY(ts,symbol))")
        for _ in range(2):
            db.execute("INSERT OR REPLACE INTO audit VALUES(?,?,?)",
                       ("2026-10-10T12:00Z", "AUSDT", "DEEP_SELECTED"))
        self.assertEqual(db.execute("SELECT COUNT(*) FROM audit").fetchone()[0], 1)

    def test_missing_is_not_rejected(self):
        self.assertEqual(classify("MISSING", set(), set(), set()), "UNOBSERVED")

if __name__ == "__main__":
    unittest.main()
