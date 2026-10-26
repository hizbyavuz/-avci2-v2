"""Incident regressions for provisional sizing, delivery and false health flips."""
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, MagicMock
import zlib

from ta_paper.core import CFG, MINUTE, sizing
from ta_paper.io import Store, Binance, Telegram, book_check, DataUnavailable
from ta_paper.observations import collect, record_setup
from ta_paper.rate_limit import request_weight
from ta_paper.runner import scan, run, STOP, update_health, continuous_path
from test_ta_paper import NOW, MARKET, setup, filled, candle
from test_ta_paper_operations import market_response, response
from test_ta_paper_scan_recovery import FakeClient


class AdmissionDeliveryTests(unittest.TestCase):
    def setUp(self):
        STOP.clear(); self.tmp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.tmp.name)/'test.db')
        self.output=StringIO(); self.capture=redirect_stdout(self.output); self.capture.__enter__()

    def tearDown(self):
        self.capture.__exit__(None,None,None)
        self.store.db.close(); self.tmp.cleanup(); STOP.clear()

    def test_final_spread_adjusted_quantity_passes_when_provisional_notional_fails(self):
        for direction in ('LONG','SHORT'):
            with self.subTest(direction=direction):
                client=FakeClient(n=1,setups=True)
                p=setup(direction);p.update(symbol='S000USDT',reason='PULLBACK_RECLAIM')
                # Less than the frozen 0.1% spread cap. No threshold is changed.
                bid,ask=99.951,100.049
                spread=(ask-bid)/100
                final=sizing(dict(p,entry_half_spread=spread/2),MARKET,1000,5)
                provisional=sizing(p,MARKET,1000,5)
                best=ask if direction=='LONG' else bid
                sign=1 if direction=='LONG' else -1
                levels=[[str(best),str(final['quantity']*1.00001)],
                        [str(best*(1+sign*.05)),'100']]
                book=dict(E=NOW,bids=[[str(bid),'100']],asks=[[str(ask),'100']])
                book['asks' if direction=='LONG' else 'bids']=levels
                # Reproduces the old false rejection on the very same book.
                with self.assertRaisesRegex(ValueError,'BOOK_IMPACT_EXCEEDS_MODEL'):
                    book_check(book,NOW,direction,provisional['notional'])
                original=client.get
                client.get=lambda endpoint,**kw:book if endpoint.endswith('/depth') else original(endpoint,**kw)
                db=Store(Path(self.tmp.name)/(direction+'.db'))
                try:
                    with patch('ta_paper.runner.assess',return_value=p):
                        result=scan(db,client)
                    self.assertEqual(result['admitted'],1)
                    plan=db.plans()[0]
                    self.assertEqual(plan['quantity'],final['quantity'])
                    self.assertLess(plan['quantity'],provisional['quantity'])
                    self.assertAlmostEqual(plan['book']['checked_quantity'],plan['quantity'])
                    self.assertLessEqual(plan['book']['impact_fraction'],CFG.entry_slippage)
                finally:db.db.close()

    def test_rejected_book_is_preserved_with_numeric_evidence_without_loosening_limit(self):
        c=FakeClient(n=1,setups=True);original=c.get
        book=dict(E=NOW,bids=[['104.99','100']],asks=[['105.01','.01'],['106','100']])
        c.get=lambda endpoint,**kw:book if endpoint.endswith('/depth') else original(endpoint,**kw)
        result=scan(self.store,c)
        self.assertEqual(result['admitted'],0)
        row=self.store.db.execute("SELECT payload FROM snapshots WHERE kind='BOOK:S000USDT'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(json.loads(zlib.decompress(row[0])),book)
        rejection=json.loads(self.store.db.execute("SELECT payload FROM events WHERE kind='ADMISSION_REJECTED'").fetchone()[0])
        self.assertEqual(rejection['reason'],'BOOK_IMPACT_EXCEEDS_MODEL')
        self.assertGreater(rejection['book']['impact_fraction'],CFG.entry_slippage)
        self.assertEqual(rejection['book']['impact_limit'],.0002)
        self.assertIn('S000USDT',self.output.getvalue())

    def test_verified_plan_is_flushed_before_next_symbol_can_block_api(self):
        c=FakeClient(n=3,setups=True,fail_symbol='S001USDT');deliveries=[]
        def flush():
            deliveries.append((len(self.store.plans()),c.blocked,c.calls[('S001USDT','30m')]))
        result=scan(self.store,c,on_plan=flush)
        self.assertFalse(result['complete']);self.assertTrue(c.blocked)
        self.assertEqual(deliveries,[(1,False,0)])

    def test_valid_book_at_weight_threshold_does_not_suppress_plan_delivery(self):
        c=FakeClient(n=1,setups=True);original=c.get
        def get(endpoint,**kw):
            result=original(endpoint,**kw)
            if endpoint.endswith('/depth'):c.blocked=True
            return result
        c.get=get;telegram=MagicMock();telegram.ready=True
        with patch('ta_paper.runner.Binance',return_value=c),patch('ta_paper.runner.Telegram',return_value=telegram):
            run(SimpleNamespace(state_root=self.tmp.name,report=False,telegram=True,once=True))
        self.assertTrue(telegram.flush.call_args.kwargs['signals_enabled'])
        db=Store(Path(self.tmp.name)/CFG.version.lower()/'research.sqlite3')
        try:self.assertEqual(len(db.plans()),1)
        finally:db.db.close()

    def test_telegram_prioritizes_valid_plan_over_health_backlog(self):
        p=filled();p.update(id='priority',status='PENDING',entry_ms=NOW+MINUTE)
        self.store.save(p)
        for i in range(6):self.store.queue('system:health:'+str(i),'health',NOW-10+i)
        self.store.queue('priority:PENDING','valid plan',NOW)
        telegram=Telegram(self.tmp.name);telegram.token='TEST';telegram.chat='123'
        bodies=[]
        def send(req,**kw):
            bodies.append(json.loads(req.data)['text'])
            return response({'ok':True,'result':{'chat':{'id':123},'message_id':len(bodies)}})
        with patch('urllib.request.build_opener') as net:
            net.return_value.open.side_effect=send
            telegram.flush(self.store,NOW)
        self.assertEqual(bodies[0],'valid plan')

    def test_slow_first_delivery_does_not_send_second_plan_after_entry_time(self):
        for name in ('a','b'):
            p=filled();p.update(id=name,status='PENDING',entry_ms=NOW+MINUTE)
            self.store.save(p);self.store.queue(name+':PENDING',name,NOW)
        telegram=Telegram(self.tmp.name);telegram.token='TEST';telegram.chat='123'
        ticks=iter([NOW,NOW+MINUTE])
        with patch('urllib.request.build_opener') as net:
            net.return_value.open.return_value=response({'ok':True,'result':{'chat':{'id':123},'message_id':1}})
            telegram.flush(self.store,NOW,clock=lambda:next(ticks))
            self.assertEqual(net.return_value.open.call_count,1)
        self.assertEqual(self.store.db.execute("SELECT state FROM outbox WHERE key='b:PENDING'").fetchone()[0],'STALE')

    def test_finished_scan_does_not_flip_waiting_ready_for_background_guard(self):
        state=dict(status='COMPLETE',scan_slot=42,universe=320,assessed=320,setups=2,
                   admitted_total=0,reasons={'ADMISSION:BOOK_IMPACT_EXCEEDS_MODEL':2})
        cooldown=dict(remaining_seconds=0,until_ms=0,source='WEIGHT_HEADER')
        update_health(self.store,'READY',cooldown,NOW,state)
        for i in range(1,5):
            update_health(self.store,'SHARED_IP_WEIGHT_HIGH',dict(cooldown,remaining_seconds=40,
                until_ms=NOW+(i+1)*MINUTE),NOW+i*MINUTE,state)
            update_health(self.store,'READY',cooldown,NOW+i*MINUTE+50_000,state)
        messages=[r[0] for r in self.store.db.execute('SELECT text FROM outbox')]
        self.assertEqual(len(messages),1)
        self.assertIn('Teknik aday: 2',messages[0]);self.assertIn('Fiyat kayması sınırı: 2',messages[0])
        update_health(self.store,'READY',cooldown,NOW+30*MINUTE,dict(state,scan_slot=43))
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM outbox').fetchone()[0],2)

    def test_real_418_after_complete_scan_still_reports_exact_deadline(self):
        state=dict(status='COMPLETE',scan_slot=42,universe=320,assessed=320,setups=0)
        update_health(self.store,'READY',dict(remaining_seconds=0),NOW,state)
        cooldown=dict(remaining_seconds=120,until_ms=NOW+120_000,source='EXCHANGE_RETRY_TIME')
        update_health(self.store,'BINANCE_HTTP_418',cooldown,NOW+1,state)
        messages=[r[0] for r in self.store.db.execute('SELECT text FROM outbox')]
        self.assertEqual(len(messages),2)
        self.assertIn('Binance geçici IP erişim engeli',messages[-1])
        self.assertIn('En erken yeniden deneme:',messages[-1])

    def test_observation_limit_matches_needed_bars_and_preserves_all_evidence(self):
        p=setup();start=p['entry_ms'];record_setup(self.store,p,NOW,MARKET)
        calls=[];now=start+3*MINUTE+3000
        def candles(symbol,tf,**params):
            calls.append(params)
            return [candle(start+i*MINUTE) for i in range(3)]
        client=SimpleNamespace(now=lambda:now,candles=candles)
        self.assertEqual(collect(self.store,client),1)
        self.assertEqual(calls[0]['limit'],3)
        self.assertEqual(request_weight('/fapi/v1/klines',calls[0]),1)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM candles WHERE tf='1m'").fetchone()[0],3)

    def test_position_path_requests_small_range_without_1000_bar_weight(self):
        start=NOW//MINUTE*MINUTE;calls=[]
        def candles(symbol,tf,**params):
            calls.append(params);return [candle(start)]
        result=continuous_path(SimpleNamespace(candles=candles),'TESTUSDT',start,start+MINUTE+3000)
        self.assertEqual(len(result),1)
        self.assertLess(calls[0]['limit'],100)
        self.assertEqual(request_weight('/fapi/v1/klines',calls[0]),1)

    def test_request_usage_separates_local_minute_from_ip_header(self):
        c=Binance();clock=[NOW];c.now=lambda:clock[0]
        response_value=market_response({'serverTime':NOW})
        response_value.__enter__.return_value.headers['X-MBX-USED-WEIGHT-1M']='800'
        with patch('urllib.request.build_opener') as net,patch.object(c.gate,'acquire'):
            net.return_value.open.return_value=response_value
            c.get('/fapi/v1/time')
            u=c.usage()
            self.assertEqual((u['local_requests_1m'],u['local_estimated_weight_1m'],u['reported_ip_weight_1m']),(1,1,800))
            clock[0]+=MINUTE
            net.return_value.open.return_value=market_response([])
            c.get('/fapi/v1/klines',symbol='TESTUSDT',interval='1m',limit=3)
            u=c.usage()
            self.assertEqual((u['local_requests_1m'],u['local_requests_since_start']),(1,2))
            self.assertEqual(u['local_request_minute'],clock[0]//MINUTE)


if __name__=='__main__':unittest.main()
