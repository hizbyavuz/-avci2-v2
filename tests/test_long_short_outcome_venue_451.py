import os
import sqlite3
import tempfile
import unittest
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import long_short_outcome_prices as p
import long_short_outcome_tracker_v21 as ot

BASE=datetime(2026,10,7,10,0,tzinfo=timezone.utc)


def gate_range(url,params):
    assert url=="/futures/usdt/candlesticks"
    assert "limit" not in params
    assert params["contract"].endswith("_USDT")
    return [{"t":t,"o":"100","h":"102","l":"99","c":"101","sum":"100"}
            for t in range(params["from"],params["to"]+1,60)]


def bybit_range(url,params):
    assert url=="/v5/market/kline"
    assert params["interval"]=="1"
    a=params["start"]; b=params["end"]
    return {"result":{"list":[[str(t),"100","102","99","101","2","200"]
                             for t in range(a,b,60000)]}}


class OutcomeVenueTests(unittest.TestCase):
    def test_gate_historical_bounds_and_provenance(self):
        with patch.object(p,"_gate",side_effect=gate_range):
            r=p.historical_1m("SOLUSDT",BASE,BASE+timedelta(minutes=15),
                              requested_source="GATE_FUTURES",
                              allow_fallback=False)
        self.assertEqual(r.source,"GATE_FUTURES")
        self.assertTrue(r.venue_matched)
        self.assertEqual(len(r),15)
        self.assertTrue(p.has_full_horizon(r,BASE,BASE+timedelta(minutes=15)))

    def test_451_falls_back_to_gate_and_marks_unmatched(self):
        called=[]
        def blocked(*args,**kwargs):
            called.append(1)
            raise RuntimeError("HTTP 451")
        with patch.object(p,"_gate",side_effect=gate_range):
            r=p.historical_1m("ETHUSDT",BASE,BASE+timedelta(minutes=15),
                              requested_source="BINANCE_SPOT",
                              fetch_binance=blocked)
        self.assertEqual(len(called),1)
        self.assertEqual(r.source,"GATE_FUTURES")
        self.assertFalse(r.venue_matched)

    def test_bybit_fallback_after_gate_unavailable(self):
        with patch.object(p,"_gate",side_effect=RuntimeError("Gate down")):
            with patch.object(p,"_bybit",side_effect=bybit_range):
                x=p.historical_1m("XRPUSDT",BASE,BASE+timedelta(minutes=15),
                                  requested_source="BINANCE_SPOT",
                                  fetch_binance=lambda *_args,**_kwargs: (_ for _ in ()).throw(RuntimeError("451")))
        self.assertEqual(x.source,"BYBIT_LINEAR")
        self.assertFalse(x.venue_matched)
        self.assertEqual(len(x),15)

    def test_gate_fail_closed_when_explicit_price_source(self):
        with patch.object(p,"_gate",side_effect=RuntimeError("Gate 451")):
            with patch.object(p,"_bybit",side_effect=AssertionError("must not substitute venue")):
                with self.assertRaisesRegex(RuntimeError,"no complete outcome price source"):
                    p.historical_1m("BTCUSDT",BASE,BASE+timedelta(minutes=15),
                                    requested_source="GATE_FUTURES",
                                    allow_fallback=False)

    def test_gap_does_not_get_profitable_label(self):
        gap=[p._normalize([[int((BASE+timedelta(minutes=i)).timestamp()*1000),"100","102","99","101"] for i in (0,1,2,5,6)],int(BASE.timestamp()*1000),int((BASE+timedelta(minutes=7)).timestamp()*1000))]
        self.assertFalse(p.has_full_horizon(gap[0],BASE,BASE+timedelta(minutes=7)))

    def test_historical_source_is_inferred_not_verified(self):
        self.assertEqual(ot._preferred_source({}),("BINANCE_SPOT",True))
        self.assertEqual(ot._preferred_source({"_live_price_source":"GATE_FUTURES"}),("GATE_FUTURES",False))
        x=p.PriceSeries([], "BINANCE_SPOT", "BINANCE_SPOT", source_inferred=True)
        self.assertFalse(x.venue_matched)

    def test_migration_preserves_legacy_proxy_rows(self):
        with sqlite3.connect(":memory:") as db:
            ot.init_db(db)
            columns={row[1] for row in db.execute("PRAGMA table_info(delivered_signal_outcomes)")}
            self.assertTrue({"price_source","price_source_matched","price_source_inferred"}<=columns)
            for table in ("delivered_signal_outcomes","shadow_blocked_outcomes",
                          "delivered_watch_outcomes","watch_no_confirm_outcomes"):
                col={r[1] for r in db.execute(f"PRAGMA table_info({table})")}
                self.assertIn("price_source",col)

    def test_source_records_are_separate_and_no_relabel(self):
        with sqlite3.connect(":memory:") as db:
            ot.init_db(db)
            db.execute("""INSERT INTO watch_no_confirm_outcomes (
                watch_episode_id,version,symbol,direction,started_at_utc,ended_at_utc,
                start_price,horizon_min,evaluated_at_utc
            ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (1,"OLD","ETHUSDT","SHORT","2026-10-07","2026-10-08",100,15,"2026-10-08"))
            db.execute("""INSERT INTO watch_no_confirm_outcomes (
                watch_episode_id,version,symbol,direction,started_at_utc,ended_at_utc,
                start_price,horizon_min,evaluated_at_utc
            ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (2,"NEW","BTCUSDT","LONG","2026-10-07","2026-10-08",100,15,"2026-10-08"))
            series=p.PriceSeries([],"GATE_FUTURES","BINANCE_SPOT")
            ot._persist_source(db,"watch_no_confirm_outcomes",
                               "watch_episode_id=? AND horizon_min=?",(2,15),series)
            saved=db.execute("SELECT watch_episode_id,price_source,price_source_matched FROM watch_no_confirm_outcomes ORDER BY watch_episode_id").fetchall()
            self.assertEqual(saved,[(1,"LEGACY_BINANCE_SPOT_PROXY",0),(2,"GATE_FUTURES",0)])


if __name__=="__main__":
    unittest.main()
