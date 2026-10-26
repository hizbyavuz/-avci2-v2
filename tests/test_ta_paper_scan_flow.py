"""Incident regressions: partial coverage, durable candle reuse, and delivery."""
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, MagicMock

from ta_paper.core import CFG, MINUTE, closed_bars, assess
from ta_paper.io import Binance, Store, Telegram, DataUnavailable
from ta_paper.rate_limit import request_weight
from ta_paper.runner import scan, run, STOP, SCAN_STATE_KEY, update_health, warm_scan_cache
from test_ta_paper import NOW, filled
from test_ta_paper_scan_recovery import FakeClient


def raw_window(period, now, limit=120, start=None):
    last=now//period*period
    start=last-(limit-1)*period if start is None else start
    out=[]
    for at in range(start,min(last+period,start+limit*period),period):
        price=round(100+(at//period)*.001,8)
        out.append([at,price,price+.2,price-.2,price,10,at+period-1])
    return out


class FlowTests(unittest.TestCase):
    def setUp(self):
        STOP.clear();self.tmp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.tmp.name)/'flow.db')
        self.output=redirect_stdout(StringIO());self.output.__enter__()
    def tearDown(self):
        self.output.__exit__(None,None,None);self.store.db.close();self.tmp.cleanup();STOP.clear()

    def test_missing_symbol_does_not_veto_verified_plan_or_fake_complete(self):
        c=FakeClient(setups=True)
        original=c.candles
        def candles(symbol,*args,**kwargs):
            if symbol=='S001USDT':raise ValueError('CANDLE_GAP_OR_DUPLICATE')
            return original(symbol,*args,**kwargs)
        c.candles=candles
        h=scan(self.store,c)
        self.assertEqual((h['assessed'],h['remaining'],h['admitted']),(2,1,1))
        self.assertFalse(h['complete'])
        self.assertEqual([p['symbol'] for p in self.store.plans()],['S000USDT'])
        self.assertEqual(h['data_errors'],{'CANDLE_GAP_OR_DUPLICATE':1})

    def test_liquidity_priority_not_exchange_listing_order(self):
        c=FakeClient(setups=True);original=c.get
        def get(path,**kwargs):
            data=original(path,**kwargs)
            if path.endswith('24hr'):data[-1]['quoteVolume']='9000000'
            return data
        c.get=get
        with patch('ta_paper.runner.SCAN_BATCH_SYMBOLS',1):h=scan(self.store,c)
        self.assertFalse(h['complete']);self.assertEqual(h['admitted'],1)
        self.assertEqual(self.store.plans()[0]['symbol'],'S002USDT')

    def test_slow_book_crossing_deadline_cannot_admit_stale_plan(self):
        c=FakeClient(n=1,setups=True);original=c.get
        def get(path,**kwargs):
            if path.endswith('depth'):c.stamp+=4*MINUTE
            return original(path,**kwargs)
        c.get=get
        h=scan(self.store,c)
        self.assertEqual(h['status'],'EXPIRED');self.assertEqual(self.store.plans(),[])

    def test_partial_runtime_can_deliver_valid_plan_without_ready_notice(self):
        c=FakeClient(n=3,setups=True);telegram=MagicMock();telegram.ready=True
        with patch('ta_paper.runner.Binance',return_value=c),patch('ta_paper.runner.Telegram',return_value=telegram),patch('ta_paper.runner.SCAN_BATCH_SYMBOLS',1):
            result=run(SimpleNamespace(state_root=self.tmp.name,report=False,telegram=True,once=True))
        self.assertEqual(result,2)  # Still honestly an incomplete scan.
        self.assertTrue(telegram.flush.call_args.kwargs['signals_enabled'])
        db=Store(Path(self.tmp.name)/CFG.version.lower()/'research.sqlite3')
        try:
            self.assertEqual(len(db.plans()),1)
            texts=[r[0] for r in db.db.execute('SELECT text FROM outbox')]
            self.assertFalse(any('Tarama tamamlandı' in x for x in texts))
        finally:db.db.close()

    def test_expired_notice_overrides_retry_timer_and_does_not_spam(self):
        state={'status':'EXPIRED','slot':NOW//CFG.entry_ms,'assessed':217,'universe':318}
        cooldown={'remaining_seconds':40,'until_ms':NOW+40_000,'source':'WEIGHT_HEADER'}
        update_health(self.store,'SHARED_IP_WEIGHT_HIGH',cooldown,NOW,state)
        update_health(self.store,'SCAN_EXPIRED',dict(cooldown,remaining_seconds=0),NOW+MINUTE,state)
        rows=self.store.db.execute('SELECT text FROM outbox').fetchall()
        self.assertEqual(len(rows),1)
        self.assertIn('Giriş süresi doldu',rows[0][0]);self.assertIn('Sonraki tarama:',rows[0][0])
        self.assertNotIn('En erken yeniden deneme:',rows[0][0])

    def test_late_pending_notice_is_not_delivered_as_fresh_entry(self):
        p=filled();p.update(id='late',status='PENDING',entry_ms=NOW+MINUTE,observed_ms=NOW)
        self.store.save(p);self.store.queue('late:PENDING','stale entry',NOW)
        telegram=Telegram(self.tmp.name);telegram.token='TEST';telegram.chat='123'
        with patch('urllib.request.build_opener') as net:
            telegram.flush(self.store,NOW+MINUTE)
            net.assert_not_called()
        self.assertEqual(self.store.db.execute('SELECT state FROM outbox').fetchone()[0],'STALE')


class CandleReuseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'cache.db'
        self.store=Store(self.path);self.now=NOW;self.requests=[]
        self.client=self.new_client()
    def tearDown(self):self.store.db.close();self.tmp.cleanup()
    def new_client(self):
        client=Binance(candle_store=self.store);client.now=lambda:self.now
        def get(endpoint,**params):
            self.requests.append((endpoint,params))
            period={'30m':CFG.entry_ms,'4h':CFG.context_ms}[params['interval']]
            return raw_window(period,self.now,params['limit'],params.get('startTime'))
        client.get=get
        return client

    def test_restart_reuses_closed_context_and_incremental_results_match_full(self):
        for tf,period in [('30m',CFG.entry_ms),('4h',CFG.context_ms)]:
            actual=self.client.candles('TESTUSDT',tf)
            self.assertEqual(actual,closed_bars(raw_window(period,self.now),period,self.now))
        self.store.db.close();self.store=Store(self.path);self.client=self.new_client()
        before=len(self.requests)
        self.client.candles('TESTUSDT','4h');self.client.candles('TESTUSDT','30m')
        self.assertEqual(len(self.requests),before)
        self.now+=CFG.entry_ms
        actual30=self.client.candles('TESTUSDT','30m')
        actual4=self.client.candles('TESTUSDT','4h')
        self.assertEqual(len(self.requests),before+1)
        self.assertEqual(self.requests[-1][1]['limit'],3)
        expected30=closed_bars(raw_window(CFG.entry_ms,self.now),CFG.entry_ms,self.now)
        expected4=closed_bars(raw_window(CFG.context_ms,self.now),CFG.context_ms,self.now)
        self.assertEqual(actual30,expected30);self.assertEqual(actual4,expected4)
        self.assertEqual(assess('TESTUSDT',actual30,actual4,self.now),assess('TESTUSDT',expected30,expected4,self.now))

    def test_four_hour_rollover_updates_context_with_one_small_request(self):
        self.client.candles('TESTUSDT','4h');self.now+=CFG.context_ms
        actual=self.client.candles('TESTUSDT','4h')
        self.assertEqual(self.requests[-1][1]['limit'],3)
        self.assertEqual(actual,closed_bars(raw_window(CFG.context_ms,self.now),CFG.context_ms,self.now))

    def test_changed_closed_overlap_is_rejected_and_original_evidence_kept(self):
        original=self.client.candles('TESTUSDT','30m');self.now+=CFG.entry_ms
        get=self.client.get
        def changed(*args,**kwargs):
            rows=get(*args,**kwargs);rows[0][5]+=1;return rows
        self.client.get=changed
        with self.assertRaisesRegex(DataUnavailable,'CLOSED_CANDLE_CHANGED'):
            self.client.candles('TESTUSDT','30m')
        self.assertEqual(self.store.recent_candles('TESTUSDT','30m',119),original)

    def test_missing_incremental_bar_cannot_be_filled_from_stale_cache(self):
        self.client.candles('TESTUSDT','30m');self.now+=2*CFG.entry_ms
        get=self.client.get
        self.client.get=lambda *a,**k:[r for i,r in enumerate(get(*a,**k)) if i!=1]
        with self.assertRaisesRegex(ValueError,'CANDLE_GAP'):
            self.client.candles('TESTUSDT','30m')

    def test_warm_full_universe_next_scan_uses_quarter_of_candle_weight(self):
        symbols=[f'S{i:03}USDT' for i in range(318)]
        for symbol in symbols:
            self.client.candles(symbol,'30m');self.client.candles(symbol,'4h')
        initial=sum(request_weight(e,p) for e,p in self.requests)
        self.assertEqual(initial,1272)
        self.requests.clear();self.now+=CFG.entry_ms
        for symbol in symbols:
            for tf,period in [('30m',CFG.entry_ms),('4h',CFG.context_ms)]:
                self.assertEqual(self.client.candles(symbol,tf),closed_bars(raw_window(period,self.now),period,self.now))
        self.assertEqual(len(self.requests),318)
        self.assertEqual(sum(request_weight(e,p) for e,p in self.requests),318)

    def test_background_warmup_is_bounded_and_skips_entry_window_and_bans(self):
        state={'markets':{f'S{i:03}USDT':{} for i in range(25)},
               'tickers':{f'S{i:03}USDT':{'quoteVolume':'3000000'} for i in range(25)}}
        self.store.setting(SCAN_STATE_KEY,json.dumps(state))
        with redirect_stdout(StringIO()):
            self.assertEqual(warm_scan_cache(self.store,self.client),0)
            self.assertEqual(self.requests,[])
            self.now+=6*MINUTE
            self.assertEqual(warm_scan_cache(self.store,self.client),12)
            self.assertEqual(len(self.requests),24)
            self.client.gate.state=lambda:{'remaining_seconds':60}
            self.assertEqual(warm_scan_cache(self.store,self.client),0)
            self.assertEqual(len(self.requests),24)


if __name__=='__main__':unittest.main()
