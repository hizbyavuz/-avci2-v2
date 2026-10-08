import sqlite3
import sys
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import long_short_setup_lifecycle as life

T=datetime(2026,10,8,0,0,tzinfo=timezone.utc)


def candidate(direction="LONG",symbol="ETHUSDT",scan=T,**overrides):
    d={
        "symbol":symbol,"direction":direction,"setup_type":"BREAKOUT",
        "trigger_level":100.0,"retest_low":99.8,"retest_high":100.2,
        "invalidation":98.0 if direction=="LONG" else 102.0,
        "tp1":103.0 if direction=="LONG" else 97.0,
        "tp2":106.0 if direction=="LONG" else 94.0,
        "source_scan":scan,
        "regime_at_create":"DOWN",
        "config_hash":"a"*64,"price_source":"GATE_FUTURES",
    }
    d.update(overrides)
    return d


class LockedSetupTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(":memory:")
        life.init_schema(self.db)

    def tearDown(self):
        self.db.close()

    def create_watch(self,**kw):
        sid,fresh=life.create_candidate(self.db,candidate(**kw),T)
        self.assertTrue(fresh)
        life.transition(self.db,sid,"WATCH",T+timedelta(seconds=1))
        return sid

    def test_01_same_direction_rescan_keeps_original_levels(self):
        sid=self.create_watch()
        again,fresh=life.create_candidate(self.db,candidate(scan=T+timedelta(minutes=5),
             trigger_level=109.0,retest_low=108,retest_high=110,invalidation=107,tp1=113,tp2=115),T+timedelta(minutes=5))
        self.assertFalse(fresh)
        self.assertEqual(again,sid)
        r=life.actionable_setup_for_symbol(self.db,"ETHUSDT")
        self.assertEqual((r["trigger_level"],r["tp1"],r["invalidation"]),(100,103,98))
        with self.assertRaisesRegex(sqlite3.DatabaseError,"locked setup"):
            self.db.execute("UPDATE setups SET tp1=999 WHERE setup_id=?",(sid,))

    def test_02_opposite_direction_closes_old(self):
        sid=self.create_watch()
        next_id,is_new=life.create_candidate(self.db,
            candidate(direction="SHORT",scan=T+timedelta(minutes=5)),
            T+timedelta(minutes=5))
        self.assertTrue(is_new)
        self.assertNotEqual(sid,next_id)
        self.assertEqual(self.db.execute("SELECT state FROM setups WHERE setup_id=?",(sid,)).fetchone()[0],"DIRECTION_FLIP")
        self.assertEqual(self.db.execute("SELECT direction FROM setups WHERE setup_id=?",(next_id,)).fetchone()[0],"SHORT")

    def test_03_watch_expiration(self):
        sid=self.create_watch()
        life.transition(self.db,sid,"WATCHLIST_EXPIRED",T+timedelta(minutes=20))
        self.assertIsNone(life.actionable_setup_for_symbol(self.db,"ETHUSDT"))
        self.assertEqual(self.db.execute("SELECT state FROM setups WHERE setup_id=?",(sid,)).fetchone()[0],"WATCHLIST_EXPIRED")

    def test_04_no_active_without_final_delivered(self):
        sid=self.create_watch()
        life.transition(self.db,sid,"CONFIRMED",T+timedelta(minutes=5))
        with self.assertRaisesRegex(ValueError,"Telegram"):
            life.transition(self.db,sid,"ACTIVE",T+timedelta(minutes=6),observed_price=100)
        with self.assertRaisesRegex(ValueError,"retest"):
            life.transition(self.db,sid,"ACTIVE",T+timedelta(minutes=6),
                observed_price=103,final_telegram_event_id=5,
                final_telegram_sent_at=T+timedelta(minutes=6))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM setup_outcomes").fetchone()[0],0)

    def test_05_valid_retest_active_and_stop(self):
        sid=self.create_watch()
        life.transition(self.db,sid,"CONFIRMED",T+timedelta(minutes=5))
        life.transition(self.db,sid,"ACTIVE",T+timedelta(minutes=6),observed_price=100,
            final_telegram_event_id=77,final_telegram_sent_at=T+timedelta(minutes=6))
        life.transition(self.db,sid,"STOP",T+timedelta(minutes=8),observed_price=97)
        self.assertEqual(self.db.execute("SELECT state FROM setups WHERE setup_id=?",(sid,)).fetchone()[0],"STOP")

    def test_06_illegal_transition_no_shortcuts(self):
        sid=self.create_watch()
        with self.assertRaisesRegex(ValueError,"illegal transition"):
            life.transition(self.db,sid,"TP1",T+timedelta(minutes=2),observed_price=101)

    def test_07_event_append_only(self):
        sid=self.create_watch()
        with self.assertRaisesRegex(sqlite3.DatabaseError,"append-only"):
            self.db.execute("UPDATE setup_events SET reason='EDITED' WHERE setup_id=?",(sid,))
        with self.assertRaisesRegex(sqlite3.DatabaseError,"cannot be deleted"):
            self.db.execute("DELETE FROM setup_events WHERE setup_id=?",(sid,))

    def test_08_regime_and_config_are_frozen(self):
        sid=self.create_watch()
        self.assertEqual(self.db.execute("SELECT regime_at_create FROM setups WHERE setup_id=?",(sid,)).fetchone()[0],"DOWN")
        with self.assertRaises(sqlite3.DatabaseError):
            self.db.execute("UPDATE setups SET config_hash='b' WHERE setup_id=?",(sid,))

    def test_09_no_entry_before_telegram_send(self):
        sid=self.create_watch()
        life.transition(self.db,sid,"CONFIRMED",T+timedelta(minutes=2))
        with self.assertRaisesRegex(ValueError,"precede"):
            life.transition(self.db,sid,"ACTIVE",T+timedelta(minutes=3),observed_price=100,
                final_telegram_event_id=8,final_telegram_sent_at=T+timedelta(minutes=3,seconds=1))

    def test_10_same_state_does_not_duplicate_event(self):
        sid=self.create_watch()
        self.assertFalse(life.transition(self.db,sid,"WATCH",T+timedelta(seconds=2)))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM setup_events WHERE setup_id=?",(sid,)).fetchone()[0],2)


if __name__=="__main__":
    unittest.main()
