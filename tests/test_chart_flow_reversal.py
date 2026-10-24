import copy
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from test_chart_first import sample, NOW, aligned_flow
from long_short_chart_flow import hourly_flow, reversal_reason, flow_note
from long_short_chart_first import assess, message
from long_short_chart_first_worker import connect, advance


class FlowReversalTests(unittest.TestCase):
    def flow_db(self):
        c=sqlite3.connect(':memory:')
        c.execute('CREATE TABLE futures_closed_klines(symbol,interval,open_ms,close_ms,open,close,quote_volume,taker_buy_quote,source)')
        end=(NOW-250)//300000*300000
        for i in range(12):
            t=end-3600000+i*300000
            # Unequal quote volumes catch accidental averaging of bar ratios.
            q=100 if i<6 else 200
            c.execute('INSERT INTO futures_closed_klines VALUES(?,?,?,?,?,?,?,?,?)',
                      ('X','5m',t,t+299999,100,101,q,q*(.7 if i<6 else .55),'BINANCE_UM_FUTURES_REST'))
        return c,end

    def test_exact_hour_weighted_share_and_no_forming_candle(self):
        c,end=self.flow_db()
        c.execute('INSERT INTO futures_closed_klines VALUES(?,?,?,?,?,?,?,?,?)',
                  ('X','5m',end,end+299999,101,500,999999,999999,'BINANCE_UM_FUTURES_REST'))
        p=hourly_flow(c,'X',NOW)
        self.assertEqual(p['status'],'OK')
        self.assertAlmostEqual(p['buy_share'],.6)
        self.assertAlmostEqual(p['price_return_pct'],1)
        self.assertIn('zayıfladı',flow_note(p,'LONG'))
        c.close()

    def test_missing_stale_corrupt_and_foreign_flow_are_not_support(self):
        for sql in ('DELETE FROM futures_closed_klines WHERE open_ms=(SELECT MIN(open_ms) FROM futures_closed_klines)',
                    'UPDATE futures_closed_klines SET taker_buy_quote=NULL',
                    'UPDATE futures_closed_klines SET taker_buy_quote=quote_volume+1',
                    "UPDATE futures_closed_klines SET source='OTHER_VENUE'"):
            c,_=self.flow_db();c.execute(sql)
            self.assertNotEqual(hourly_flow(c,'X',NOW)['status'],'OK');c.close()
        c,_=self.flow_db()
        self.assertNotEqual(hourly_flow(c,'X',NOW+300000)['status'],'OK');c.close()

    def test_both_directions_need_flow_and_price_but_keep_geometry(self):
        for short in (False,True):
            frames,price=sample(short);opposite,_=sample(not short);frames['4h']=opposite['4h']
            side='SHORT' if short else 'LONG'
            flow=aligned_flow(short)
            p=assess('X',frames,price,50e6,NOW,flow=flow)
            self.assertEqual(p['stage'],'READY',p)
            self.assertEqual(p['scenario'],'REVERSAL_ATTEMPT')
            self.assertIn('ANA YÖNE TERS',message(p))
            self.assertIn('Son 1 saat',message(p))
            self.assertNotIn('ana yönde çıkış',message(p))
            for bad in (None,dict(flow,buy_share=.5),dict(flow,price_return_pct=0),
                        dict(flow,buy_share=1-flow['buy_share']),dict(flow,price_return_pct=-flow['price_return_pct'])):
                self.assertTrue(assess('X',frames,price,50e6,NOW,flow=bad)['reason'].startswith('REVERSAL_'))
            late=price+(-2 if short else 2)
            self.assertEqual(assess('X',frames,late,50e6,NOW,flow=flow)['reason'],'ENTRY_ALREADY_EXTENDED')
            with patch('long_short_chart_first.first_obstacle',return_value=(price+.01*(-1 if short else 1),'5m')):
                self.assertNotEqual(assess('X',frames,price,50e6,NOW,flow=flow)['stage'],'READY')

    def test_flow_does_not_override_unclear_hourly_structure(self):
        frames,price=sample()
        from long_short_chart_first import describe
        calls=[describe(frames[tf]) for tf in ('5m','15m','1h','4h')]
        calls[2]['direction']='RANGE';calls.append(dict(calls[3]))
        with patch('long_short_chart_first.describe',side_effect=calls):
            p=assess('X',frames,price,50e6,NOW,flow=dict(status='OK',buy_share=.9,price_return_pct=5))
        self.assertEqual(p['reason'],'HOURLY_STRUCTURE_UNCLEAR')

    def test_pressure_without_price_response_is_explicit(self):
        self.assertIn('fiyat karşılık vermiyor',flow_note(dict(status='OK',buy_share=.8,price_return_pct=-1),'LONG'))
        self.assertIn('desteklemiyor',flow_note(dict(status='OK',buy_share=.4,price_return_pct=1),'LONG'))

    def test_pending_reversal_revalidates_flow_before_sending_ready(self):
        for allowed in (False,True):
            with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'LS_STATE_DIR':directory}),patch('time.time',return_value=NOW/1000):
                c=connect();k=sqlite3.connect(':memory:')
                k.executescript('CREATE TABLE futures_last_price(symbol,price,event_ms,received_ms,source);'
                               'CREATE TABLE futures_closed_klines(symbol,interval,close,close_ms,open_ms);')
                frames,price=sample();opp,_=sample(True);frames['4h']=opp['4h']
                flow=aligned_flow()
                p=assess('X',frames,price,50e6,NOW,flow=flow);p.update(stage='WATCH',created_ms=NOW-60000)
                k.execute('INSERT INTO futures_last_price VALUES(?,?,?,?,?)',('X',price,NOW-100,NOW-100,'BINANCE_UM_FUTURES_WEBSOCKET'))
                k.executemany('INSERT INTO futures_closed_klines VALUES(?,?,?,?,?)',[
                    ('X','5m',price,NOW-8000,NOW-308000),('X','5m',p['trigger']-.01,NOW-308000,NOW-608000)])
                c.execute('INSERT INTO episodes VALUES(?,?,?,?,?,?,?,?)',('e','X','LONG',NOW-60000,'WATCH','WATCH',None,json.dumps(p)));c.commit()
                decision=copy.deepcopy(p)
                if not allowed:
                    decision.update(reason='REVERSAL_FLOW_NOT_ALIGNED',hourly_flow=dict(flow,buy_share=.4))
                publisher=Mock(return_value={'status':'ACK'})
                with patch('long_short_chart_first_worker.path_guard',return_value={'status':'CLEAR'}):
                    advance(c,k,{'X':decision},NOW,publisher,'token','123')
                self.assertEqual(c.execute('SELECT stage FROM episodes').fetchone()[0],'READY' if allowed else 'WATCH')
                self.assertEqual(publisher.call_count,int(allowed))
                if allowed:self.assertIn('ANA YÖNE TERS',publisher.call_args.args[4])
                c.close();k.close()
