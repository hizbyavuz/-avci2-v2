import sqlite3
import tempfile
import unittest
from pathlib import Path
from long_short_move_context import snapshot, WINDOWS


class MoveContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name)/'candles.db'
        self.now = 14400000*100
        with sqlite3.connect(self.db) as c:
            c.execute('CREATE TABLE futures_closed_klines(symbol,interval,open_ms,close_ms,open,high,low,close,quote_volume,taker_buy_quote,source)')
            for iv, (step, n) in WINDOWS.items():
                for i in range(n):
                    t = self.now-(n-i)*step
                    p = 100+i*.1
                    c.execute('INSERT INTO futures_closed_klines VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                              ('ABC',iv,t,t+step-1,p,p+.2,p-.2,p+.1,100,60,'BINANCE_UM_FUTURES_WEBSOCKET'))

    def read(self, side='LONG'):
        return snapshot(self.db,'ABC',side,110,self.now)

    def test_future_and_unclosed_candles_cannot_change_history(self):
        before=self.read()
        self.assertEqual(before['status'],'OK')
        with sqlite3.connect(self.db) as c:
            for iv,(step,_) in WINDOWS.items():
                c.execute('INSERT INTO futures_closed_klines VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    ('ABC',iv,self.now,self.now+step-1,500,600,400,550,100,90,'BINANCE_UM_FUTURES_WEBSOCKET'))
        self.assertEqual(before,self.read())

    def test_short_long_symmetry_and_unknown_obstacle(self):
        l,s=self.read(),self.read('SHORT')
        for iv in WINDOWS:
            a,b=l['timeframes'][iv],s['timeframes'][iv]
            self.assertEqual(a['range_position'],b['range_position'])
            for key in ('directional_move_before_event_pct','directional_ema_distance_atr','directional_flow_change'):
                self.assertAlmostEqual(a[key],-b[key])
            self.assertIsNone(a['directional_pivot_room_pct'])
            self.assertEqual(a['obstacle_status'],'UNKNOWN')

    def test_gap_does_not_produce_valid_metrics(self):
        with sqlite3.connect(self.db) as c:
            c.execute("DELETE FROM futures_closed_klines WHERE interval='1h' AND open_ms=?",(self.now-10*3600000,))
        r=self.read()
        self.assertEqual(r['status'],'INCOMPLETE')
        self.assertEqual(r['timeframes']['1h']['status'],'MISSING_HISTORY')
        self.assertNotIn('range_position',r['timeframes']['1h'])

    def test_unverified_and_invalid_candles_rejected(self):
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE futures_closed_klines SET source='SPOT' WHERE interval='4h'")
            c.execute("UPDATE futures_closed_klines SET taker_buy_quote=101 WHERE interval='1h'")
        r=self.read()
        for iv in ('4h','1h'):
            self.assertEqual(r['timeframes'][iv]['status'],'UNVERIFIED_HISTORY')

    def test_missing_and_invalid_reference_explicit(self):
        self.assertEqual(snapshot(self.db,'ABC','SHORT',None,self.now)['status'],'INVALID_REFERENCE')
        self.assertEqual(snapshot(self.db.parent/'absent','ABC','LONG',1,self.now)['status'],'MISSING_CANDLE_DB')
        self.assertFalse((self.db.parent/'absent').exists())


if __name__=='__main__': unittest.main()
