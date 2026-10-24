from test_chart_first import aligned_flow
import copy
import importlib
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_chart_first import sample, NOW
import long_short_chart_first as chart
from long_short_chart_lifecycle import path_guard
from long_short_chart_first_worker import connect, advance
from long_short_futures_kline_shadow import initialize, FuturesKlineStream, parse_forming_range


class LifecycleTests(unittest.TestCase):
    def db(self, short=False, partial=False):
        k=sqlite3.connect(':memory:')
        k.executescript('''CREATE TABLE futures_closed_klines(symbol,interval,open_ms,close_ms,open,high,low,close,source);
            CREATE TABLE futures_current_1m(symbol,open_ms,close_ms,open,high,low,close,source,event_ms,received_ms);
            CREATE TABLE futures_last_price(symbol,price,event_ms,received_ms,source);''')
        f,price=sample(short);p=chart.assess('XUSDT',f,price,50e6,NOW,flow=aligned_flow(short))
        end=NOW//60000*60000;p.update(stage='WATCH',created_ms=end-120000+(1000 if partial else 0))
        for t in range(end-120000,end,60000):
            k.execute('INSERT INTO futures_closed_klines VALUES(?,?,?,?,?,?,?,?,?)',
                      ('XUSDT','1m',t,t+59999,price,price+.001,price-.001,price,'BINANCE_UM_FUTURES_WEBSOCKET'))
        k.execute('INSERT INTO futures_current_1m VALUES(?,?,?,?,?,?,?,?,?,?)',
                  ('XUSDT',end,end+59999,price,price+.001,price-.001,price,'BINANCE_UM_FUTURES_WEBSOCKET',NOW-100,NOW-100))
        k.execute('INSERT INTO futures_last_price VALUES(?,?,?,?,?)',('XUSDT',price,NOW-100,NOW-100,'BINANCE_UM_FUTURES_WEBSOCKET'))
        for t,cl in [(NOW-308000,price),(NOW-608000,p['trigger']+(.01 if short else -.01))]:
            k.execute('INSERT INTO futures_closed_klines VALUES(?,?,?,?,?,?,?,?,?)',
                ('XUSDT','5m',t,t+299999,cl,cl+.001,cl-.001,cl,'BINANCE_UM_FUTURES_WEBSOCKET'))
        return k,p

    def test_closed_and_forming_stop_recovery_both_directions(self):
        for short in (False,True):
            for forming in (False,True):
                k,p=self.db(short)
                field='high' if short else 'low';value=p['stop']+(.01 if short else -.01)
                table='futures_current_1m' if forming else 'futures_closed_klines'
                where='' if forming else " WHERE interval='1m'"
                k.execute(f'UPDATE {table} SET {field}=?'+where,(value,))
                self.assertEqual(path_guard(k,p,NOW)['status'],'BROKEN')
                k.close()

    def test_target_recovery_and_stop_before_target(self):
        k,p=self.db();k.execute("UPDATE futures_closed_klines SET high=? WHERE interval='1m'",(p['obstacle']+.1,))
        self.assertEqual(path_guard(k,p,NOW)['status'],'OBSTACLE_REACHED');k.close()
        k,p=self.db();k.execute("UPDATE futures_closed_klines SET low=? WHERE interval='1m' AND open_ms=?",(p['stop']-.1,p['created_ms']))
        k.execute('UPDATE futures_current_1m SET high=?',(p['obstacle']+.1,))
        self.assertEqual(path_guard(k,p,NOW)['status'],'BROKEN');k.close()

    def test_boundary_and_missing_order_are_explicit_not_fabricated(self):
        k,p=self.db(partial=True)
        k.execute("UPDATE futures_closed_klines SET low=? WHERE interval='1m'",(p['stop']-.1,))
        self.assertEqual(path_guard(k,p,NOW)['status'],'AMBIGUOUS');k.close()
        k,p=self.db();k.execute("DELETE FROM futures_closed_klines WHERE interval='1m' AND open_ms=?",(p['created_ms'],))
        self.assertEqual(path_guard(k,p,NOW)['status'],'MISSING')
        k.execute('UPDATE futures_current_1m SET low=?',(p['stop']-.1,))
        self.assertEqual(path_guard(k,p,NOW)['status'],'AMBIGUOUS');k.close()

    def test_stale_or_foreign_current_bar_blocks_new_entry(self):
        for sql in ("UPDATE futures_current_1m SET event_ms=0", "UPDATE futures_current_1m SET source='OTHER'"):
            k,p=self.db();k.execute(sql)
            self.assertEqual(path_guard(k,p,NOW)['status'],'MISSING');k.close()

    def test_advance_never_revives_crossed_stop_and_survives_restart(self):
        for crossed in (False,True):
            with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{'LS_STATE_DIR':root}),patch('time.time',return_value=NOW/1000):
                c=connect();k,p=self.db()
                if crossed:k.execute("UPDATE futures_closed_klines SET low=? WHERE interval='1m'",(p['stop']-.1,))
                c.execute('INSERT INTO episodes VALUES(?,?,?,?,?,?,?,?)',('e','XUSDT','LONG',p['created_ms'],'WATCH','WATCH',None,json.dumps(p)));c.commit()
                publisher=Mock(return_value={'status':'ACK'})
                advance(c,k,{'XUSDT':p},NOW,publisher,'token','123')
                row=c.execute('SELECT stage,ended_ms,payload FROM episodes').fetchone()
                self.assertEqual(row[0],'BROKEN' if crossed else 'READY')
                if crossed:
                    self.assertIsNotNone(row[1]);self.assertNotIn('GİRİŞ KOŞULU OLUŞTU',publisher.call_args.args[4])
                    c.close();c=connect();publisher.reset_mock()
                    advance(c,k,{'XUSDT':p},NOW,publisher,'token','123');publisher.assert_not_called()
                c.close();k.close()

    def test_missing_path_holds_watch_instead_of_notifying_ready(self):
        with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{'LS_STATE_DIR':root}),patch('time.time',return_value=NOW/1000):
            c=connect();k,p=self.db();k.execute('DELETE FROM futures_current_1m')
            c.execute('INSERT INTO episodes VALUES(?,?,?,?,?,?,?,?)',('e','XUSDT','LONG',p['created_ms'],'WATCH','WATCH',None,json.dumps(p)));c.commit()
            publisher=Mock();advance(c,k,{'XUSDT':p},NOW,publisher,'token','123')
            self.assertEqual(c.execute('SELECT stage FROM episodes').fetchone()[0],'WATCH')
            publisher.assert_not_called();c.close();k.close()

    def test_failed_completion_message_cannot_reopen_terminal_episode(self):
        with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{'LS_STATE_DIR':root}),patch('time.time',return_value=NOW/1000):
            c=connect();k,p=self.db()
            k.execute("UPDATE futures_closed_klines SET low=? WHERE interval='1m'",(p['stop']-.1,))
            c.execute('INSERT INTO episodes VALUES(?,?,?,?,?,?,?,?)',('e','XUSDT','LONG',p['created_ms'],'WATCH','WATCH',None,json.dumps(p)));c.commit()
            advance(c,k,{'XUSDT':p},NOW,Mock(side_effect=RuntimeError('offline')),'token','123')
            self.assertEqual(c.execute('SELECT stage FROM episodes').fetchone()[0],'BROKEN')
            k.execute("DELETE FROM futures_closed_klines WHERE interval='1m'")
            publisher=Mock(return_value={'status':'ACK'})
            advance(c,k,{'XUSDT':p},NOW,publisher,'token','123')
            self.assertEqual(c.execute('SELECT stage FROM episodes').fetchone()[0],'BROKEN')
            self.assertNotIn('GİRİŞ KOŞULU OLUŞTU',publisher.call_args.args[4])
            c.close();k.close()

    def test_render_edit_does_not_change_identity_but_decision_edit_does(self):
        original=chart.STRATEGY_ID;read=Path.read_bytes
        def presentation(path):
            content=read(path)
            return content+b'\n# presentation edit' if path.name=='long_short_chart_messages.py' else content
        try:
            with patch.object(Path,'read_bytes',presentation):
                importlib.reload(chart);self.assertEqual(chart.STRATEGY_ID,original)
            def decision(path):
                content=read(path)
                return content+b'\n# decision edit' if path.name=='long_short_chart_lifecycle.py' else content
            with patch.object(Path,'read_bytes',decision):
                importlib.reload(chart);self.assertNotEqual(chart.STRATEGY_ID,original)
        finally:importlib.reload(chart)

    def test_forming_range_is_separate_from_closed_entry_candles(self):
        packet={'e':'kline','E':120100,'s':'XUSDT','k':{'i':'1m','t':120000,'T':179999,'o':'100','h':'101','l':'98','c':'100','x':False}}
        with tempfile.TemporaryDirectory() as root:
            k=initialize(root+'/k.db');stream=FuturesKlineStream(root+'/k.db');stream.subscribed=['XUSDT']
            stream._handle(json.dumps(packet));self.assertTrue(stream.flush(k))
            self.assertEqual(k.execute('SELECT low FROM futures_current_1m').fetchone()[0],98)
            self.assertEqual(k.execute('SELECT COUNT(*) FROM futures_closed_klines').fetchone()[0],0)
            old=copy.deepcopy(packet);old['E']=120050;old['k']['l']='99'
            stream._handle(json.dumps(old));stream.flush(k)
            self.assertEqual(k.execute('SELECT low FROM futures_current_1m').fetchone()[0],98)
            k.close()
        packet['k']['l']='102';self.assertIsNone(parse_forming_range(packet))
