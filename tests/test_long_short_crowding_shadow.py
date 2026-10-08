import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import long_short_crowding_shadow as c
import long_short_v32_shadow as v32


class AccountCrowdingTests(unittest.TestCase):
    def gate_payload(self, ratio=3.0):
        return {
            "data_mode": "MULTI_VENUE_PERP_PLUS_BINANCE_SPOT",
            "derivatives_ready": True,
            "derivatives_selected_provider": "GATE_FUTURES",
            "long_short_ratio": ratio,
            "multi_venue_derivatives": {
                "source_consistent": True, "long_short_ratio": ratio,
                "field_source": {"long_short_ratio": "GATE_FUTURES"}
            },
            "oi": {"oi_change_1h": -2.1}, "taker_ratio": .72,
            "funding_pct": .01, "v3": {"direction": "LONG"}
        }

    def test_75_percent_long_price_falling_is_opposite_short(self):
        obs = c.crowding_features(
            self.gate_payload(), {"price_change_15m_pct": -1.3}, "GATE_FUTURES")
        self.assertAlmostEqual(obs["long_account_share"], .75)
        self.assertEqual(obs["classification"], "AGAINST_LONGS")
        self.assertEqual(obs["opposite_direction"], "SHORT")
        self.assertEqual(obs["data_quality"], "SAME_VENUE")
        self.assertEqual(obs["oi_change_1h_pct"], -2.1)

    def test_short_majority_price_rising_is_opposite_long(self):
        payload = self.gate_payload(1.0/3.0)
        obs = c.crowding_features(payload, {"price_change_15m_pct": 1.7},
                                  "GATE_FUTURES")
        self.assertAlmostEqual(obs["long_account_share"], .25)
        self.assertEqual(obs["classification"], "AGAINST_SHORTS")
        self.assertEqual(obs["opposite_direction"], "LONG")

    def test_missing_derivatives_not_converted_to_fake_balanced_ratio(self):
        payload = self.gate_payload(1.0)
        payload["derivatives_ready"] = False
        obs = c.crowding_features(payload, {"price_change_15m_pct": -1.2},
                                  "GATE_FUTURES")
        self.assertIsNone(obs["ratio"])
        self.assertEqual(obs["classification"], "UNKNOWN")
        self.assertEqual(obs["data_quality"], "MISSING_RATIO_OR_UNVERIFIED")

    def test_different_venues_are_diagnostic_only(self):
        obs = c.crowding_features(
            self.gate_payload(), {"price_change_15m_pct": -1.2}, "BINANCE_SPOT")
        self.assertEqual(obs["classification"], "AGAINST_LONGS")
        self.assertEqual(obs["data_quality"], "CROSS_VENUE_DIAGNOSTIC")

    def test_field_venue_mismatch_disables_ratio(self):
        p = self.gate_payload()
        p["multi_venue_derivatives"]["field_source"]["long_short_ratio"] = "BYBIT_LINEAR"
        obs = c.crowding_features(p, {"price_change_15m_pct": -1.2}, "GATE_FUTURES")
        self.assertEqual(obs["data_quality"], "MISSING_RATIO_OR_UNVERIFIED")
        self.assertIsNone(obs["ratio"])

    def test_same_side_trend_is_control_not_contrarian(self):
        obs = c.crowding_features(
            self.gate_payload(), {"price_change_15m_pct": 1.0}, "GATE_FUTURES")
        self.assertEqual(obs["classification"], "WITH_MAJORITY")
        self.assertIsNone(obs["opposite_direction"])

    def test_nonfinite_or_invalid_ratio_unknown(self):
        for ratio in (-2, 0, "nan", "inf", None):
            obs = c.crowding_features(
                self.gate_payload(ratio), {"price_change_15m_pct": -1.0},
                "GATE_FUTURES")
            self.assertIsNone(obs["long_account_share"])

    def test_append_only_duplicate_does_not_refetch_price(self):
        with tempfile.TemporaryDirectory() as td:
            db = str(Path(td)/"research.db")
            a = {"scan_time_utc": "2026-10-08T00:00:00+00:00",
                 "symbol": "ETHUSDT", "status": "NO_TRADE"}
            prices = []
            def fetch(sym, venue):
                prices.append((sym, venue))
                return 100.0
            f = {"price_change_15m_pct": -1.0}
            self.assertTrue(c.observe(db,a,self.gate_payload(),f,"GATE_FUTURES",fetch))
            self.assertFalse(c.observe(db,a,self.gate_payload(),f,"GATE_FUTURES",fetch))
            self.assertEqual(prices, [("ETHUSDT","GATE_FUTURES")])
            with sqlite3.connect(db) as con:
                self.assertEqual(con.execute(
                    "SELECT COUNT(*) FROM crowding_observations").fetchone()[0],1)

    def test_only_due_horizon_and_directional_success_after_entry(self):
        with tempfile.TemporaryDirectory() as td:
            db = str(Path(td)/"research.db")
            scan = "2026-10-08T00:00:00+00:00"
            a = {"scan_time_utc": scan, "symbol": "ETHUSDT", "status": "NO_TRADE"}
            with patch.object(c,"utc_now",return_value=scan):
                c.observe(db,a,self.gate_payload(),{"price_change_15m_pct":-1.0},
                          "GATE_FUTURES",lambda *args: 100.0)
            start=datetime.fromisoformat(scan).timestamp()
            self.assertEqual(c.label_due(db,lambda *args: 99.0,start+14*60),0)
            self.assertEqual(c.label_due(db,lambda *args: 99.0,start+16*60),1)
            self.assertEqual(c.label_due(db,lambda *args: 99.0,start+16*60),0)
            with sqlite3.connect(db) as con:
                row=con.execute("SELECT horizon_min,status,raw_return_pct,opposite_correct FROM crowding_outcomes").fetchone()
                self.assertEqual(row[:2],(15,"VALID"))
                self.assertAlmostEqual(row[2],-1.0)
                self.assertEqual(row[3],1)

    def test_late_label_becomes_data_gap_not_a_win(self):
        with tempfile.TemporaryDirectory() as td:
            db = str(Path(td)/"research.db")
            scan="2026-10-08T00:00:00+00:00"
            with patch.object(c,"utc_now",return_value=scan):
                c.observe(db,{"scan_time_utc":scan,"symbol":"SOLUSDT","status":"WAIT"},
                    self.gate_payload(),{"price_change_15m_pct":-2.0},
                    "GATE_FUTURES",lambda *args: 100.0)
            start=datetime.fromisoformat(scan).timestamp()
            def never_fetch(*args):
                self.fail("too-late label must not call price source")
            self.assertEqual(c.label_due(db,never_fetch,start+30*60),1)
            with sqlite3.connect(db) as con:
                row=con.execute("SELECT status,raw_return_pct,opposite_correct FROM crowding_outcomes").fetchone()
            self.assertEqual(row,("DATA_GAP_LATE",None,None))

    def test_cross_venue_cannot_be_labeled_as_valid(self):
        with tempfile.TemporaryDirectory() as td:
            db = str(Path(td)/"research.db")
            scan="2026-10-08T00:00:00+00:00"
            with patch.object(c,"utc_now",return_value=scan):
                c.observe(db,{"scan_time_utc":scan,"symbol":"SOLUSDT","status":"WAIT"},
                    self.gate_payload(),{"price_change_15m_pct":-1.0},
                    "BINANCE_SPOT",lambda *args: self.fail("no crossed entry"))
            start=datetime.fromisoformat(scan).timestamp()
            self.assertEqual(c.label_due(db,lambda *args:100.0,start+3600),0)

    def test_micro_uses_only_closed_price_and_real_change(self):
        rows=[]
        for i in range(50):
            cval=100.0 + i*.02
            rows.append([i*300000,str(cval),str(cval+.1),str(cval-.1),
                         str(cval),"100",i*300000+299999,"1000",100,"0","500","0"])
        m=v32.micro_features(rows)
        self.assertAlmostEqual(m["price_change_15m_pct"],
                               (100.98/100.92-1.0)*100.0, places=5)

    def test_isolated_module_never_sends_or_orders(self):
        source=Path(c.__file__).read_text()
        self.assertNotIn("send_telegram(", source)
        self.assertNotIn("place_order(", source)
        self.assertNotIn("long_short_live_pool", source)


if __name__=="__main__":
    unittest.main()
