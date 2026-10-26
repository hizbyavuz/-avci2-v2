from contextlib import redirect_stdout
from io import BytesIO, StringIO
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error

from ta_paper.core import CFG, MINUTE, closed_bars
from ta_paper.io import Binance, Store, Telegram, DataUnavailable
from ta_paper.external_watch import BybitPublic, WatchScanner, ExternalWatch, contract, reference_universe
from ta_paper.runner import STOP, run
from ta_paper.watch import publish_watches, watch_message
from test_ta_paper import NOW, MARKET, series
from test_ta_paper_scan_recovery import FakeClient

RAW_MARKET=dict(symbol='TESTUSDT',baseCoin='TEST',quoteCoin='USDT',settleCoin='USDT',
                status='Trading',contractType='LinearPerpetual',launchTime='1',isPreListing=False,
                marketRegion='',underlyingTicker='',symbolType='',tags=[])


class Response:
    def __init__(self,data,headers=None):self.data=json.dumps(data).encode();self.headers=headers or {}
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self,*args):return self.data


class ExternalTests(unittest.TestCase):
    def setUp(self):
        STOP.clear();self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.primary=Store(self.root/'binance.sqlite3')
        self.primary.snapshot('UNIVERSE',{'metadata':{'symbols':[MARKET]},'tickers':[]},NOW)
        self.store=Store(self.root/'external_bybit'/'research.sqlite3')
        self.stop=threading.Event();self.client=BybitPublic(self.store,self.stop)
        self.client.now=lambda:NOW
        self.output=redirect_stdout(StringIO());self.output.__enter__()

    def tearDown(self):
        self.output.__exit__(None,None,None);self.stop.set()
        self.store.db.close();self.primary.db.close();self.tmp.cleanup();STOP.clear()

    def fixture(self,setup=True):
        f=FakeClient(n=1,setups=setup)
        def request(path,**params):
            if path.endswith('instruments-info'):
                return dict(time=NOW,result=dict(category='linear',list=[RAW_MARKET],nextPageCursor=''))
            if path.endswith('tickers'):
                return dict(time=NOW,result=dict(category='linear',list=[dict(symbol='TESTUSDT',turnover24h='3000000')]))
            tf='4h' if params['interval']=='240' else '30m'
            rows=f.candles('S000USDT',tf)[-params['limit']:]
            # Official Bybit reverse order and quote turnover at index 6.
            return dict(time=NOW,result=dict(category='linear',symbol=params['symbol'],
                        list=[[str(r[0]),*map(str,r[1:6]),str(float(r[4])*float(r[5]))] for r in reversed(rows)]))
        return request

    def scanner(self):return WatchScanner(self.store,self.client,self.root/'binance.sqlite3',self.stop)

    def test_bybit_candidate_during_binance_ban_without_touching_binance_data(self):
        binance=Binance(self.root/'binance_cooldown.json',candle_store=self.primary)
        until=int(time.time()*1000)+86400000
        binance.gate.block(until,'BINANCE_HTTP_418','EXCHANGE_RETRY_TIME')
        before=(self.root/'binance_cooldown.json').read_bytes()
        with patch.object(self.client,'request',side_effect=self.fixture()),patch.object(Binance,'get',side_effect=AssertionError('Binance accessed')):
            scanner=self.scanner();scanner.step()
        self.assertEqual(scanner.state['status'],'COMPLETE')
        self.assertEqual(self.store.plans(),[])
        self.assertEqual(self.primary.plans(),[])
        self.assertEqual(self.primary.db.execute('SELECT count(*) FROM assessments').fetchone()[0],0)
        self.assertEqual((self.root/'binance_cooldown.json').read_bytes(),before)
        candidate=self.store.db.execute('SELECT * FROM watch_candidates').fetchone()
        p=json.loads(candidate['payload'])
        self.assertTrue(candidate['id'].startswith('BYBIT:'))
        self.assertEqual(p['source_venue'],'BYBIT');self.assertFalse(p['binance_entry_verified'])
        self.assertEqual(p['direction'],'LONG');self.assertEqual(p['risk_reason'],'EXTERNAL_VENUE_WATCH_ONLY')
        msg=watch_message(p)
        self.assertIn('BYBIT USDT',msg);self.assertIn('Binance girişi doğrulanmadı',msg)
        self.assertLess(len(msg),1024)
        self.assertIsNotNone(self.store.db.execute("SELECT photo FROM outbox WHERE key LIKE 'watch:%'").fetchone()[0])

    def test_restart_deduplicates_and_does_not_refetch_completed_scan(self):
        with patch.object(self.client,'request',side_effect=self.fixture()) as req:
            self.scanner().step();count=req.call_count
            self.scanner().step();self.assertEqual(req.call_count,count)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM watch_candidates').fetchone()[0],1)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM outbox WHERE key LIKE 'watch:%'").fetchone()[0],1)

    def test_no_setup_is_complete_zero_candidate_not_forced_signal(self):
        with patch.object(self.client,'request',side_effect=self.fixture(False)):
            scanner=self.scanner();scanner.step()
        self.assertEqual(scanner.state['status'],'COMPLETE')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM watch_candidates').fetchone()[0],0)

    def test_bybit_cooldown_does_not_attempt_or_blame_untouched_symbols(self):
        self.client.gate.block(int(time.time()*1000)+600000,'BYBIT_403','CONSERVATIVE_FALLBACK')
        with patch.object(self.client,'request') as req:
            scanner=self.scanner()
            with self.assertRaisesRegex(DataUnavailable,'BYBIT_COOLDOWN'):scanner.step()
            req.assert_not_called()
        self.assertEqual(scanner.state,{})

    def test_missing_peer_does_not_suppress_valid_candidate_or_claim_full_scan(self):
        self.primary.snapshot('UNIVERSE',{'metadata':{'symbols':[MARKET,dict(MARKET,symbol='GAPUSDT',baseAsset='GAP')]},'tickers':[]},NOW)
        fixture=self.fixture()
        def with_gap(path,**params):
            if params.get('symbol')=='GAPUSDT':raise DataUnavailable('GAP')
            result=fixture(path,**params)
            if path.endswith('instruments-info'):result['result']['list'].append(dict(RAW_MARKET,symbol='GAPUSDT',baseCoin='GAP'))
            if path.endswith('tickers'):result['result']['list'].append(dict(symbol='GAPUSDT',turnover24h='2500000'))
            return result
        with patch.object(self.client,'request',side_effect=with_gap):
            scanner=self.scanner();scanner.step()
        self.assertEqual(scanner.state['status'],'PENDING')
        self.assertEqual(list(scanner.state['done']),['TESTUSDT'])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM watch_candidates').fetchone()[0],1)

    def test_bybit_format_converts_turnover_and_reuses_cache(self):
        with patch.object(self.client,'request',side_effect=self.fixture()) as req:
            rows=self.client.candles('TESTUSDT','30m')
            self.assertEqual(len(rows),119)
            self.assertTrue(all(r[6]==r[0]+CFG.entry_ms-1 for r in rows))
            self.assertEqual(self.client.candles('TESTUSDT','30m'),rows)
            self.assertEqual(req.call_count,1)

    def test_wrong_market_and_missing_bar_are_not_candidates(self):
        fixture=self.fixture()
        for problem in ('wrong_symbol','gap'):
            def bad(path,**params):
                response=fixture(path,**params)
                if path.endswith('kline'):
                    if problem=='wrong_symbol':response['result']['symbol']='OTHERUSDT'
                    else:response['result']['list'].pop(40)
                return response
            with patch.object(self.client,'request',side_effect=bad):
                with self.assertRaises((DataUnavailable,ValueError)):self.client.candles('TESTUSDT','30m')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM candles').fetchone()[0],0)

    def test_revised_overlap_is_rejected(self):
        rows=closed_bars(FakeClient(n=1,setups=True).candles('S000USDT','30m'),CFG.entry_ms,NOW)[-119:]
        self.store.candles('TESTUSDT','30m',rows[:-1])
        fixture=self.fixture()
        def changed(path,**params):
            value=fixture(path,**params)
            value['result']['list'][-1][5]='999'
            return value
        with patch.object(self.client,'request',side_effect=changed):
            with self.assertRaisesRegex(ValueError,'CLOSED_CANDLE_CHANGED'):self.client.candles('TESTUSDT','30m')

    def test_reference_matching_excludes_wrong_assets_tradfi_prelisting_st_and_new(self):
        self.assertIsNotNone(contract(RAW_MARKET,{'TESTUSDT':MARKET},NOW))
        for change in (dict(baseCoin='OTHER'),dict(symbol='1000TESTUSDT'),dict(marketRegion='US'),
                       dict(underlyingTicker='NVDA'),dict(isPreListing=True),dict(tags=['ST']),
                       dict(launchTime=str(NOW-86400000)),dict(status='Settling'),
                       dict(contractType='LinearFutures'),dict(quoteCoin='USDC')):
            self.assertIsNone(contract(dict(RAW_MARKET,**change),{'TESTUSDT':MARKET},NOW))

    def test_full_paginated_universe_not_first_page_only(self):
        reference={f'T{i}USDT':dict(MARKET,symbol=f'T{i}USDT',baseAsset=f'T{i}') for i in range(1100)}
        def request(path,**params):
            if path.endswith('instruments-info'):
                indexes=range(1000,1100) if params.get('cursor') else range(1000)
                return dict(time=NOW,result=dict(category='linear',nextPageCursor='' if params.get('cursor') else 'page2',
                    list=[dict(RAW_MARKET,symbol=f'T{i}USDT',baseCoin=f'T{i}') for i in indexes]))
            return dict(time=NOW,result=dict(category='linear',list=[dict(symbol=s,turnover24h='3000000') for s in reference]))
        with patch.object(self.client,'request',side_effect=request) as req:
            markets,_,_=self.client.universe(reference)
        self.assertEqual(len(markets),1100);self.assertEqual(req.call_count,3)

    def test_expired_window_prepares_data_but_never_backfills_candidate(self):
        with patch.object(self.client,'request',side_effect=self.fixture()):
            scanner=self.scanner();scanner.prepare(NOW//CFG.entry_ms)
            self.client.now=lambda:NOW+6*MINUTE
            scanner.step()
        self.assertEqual(scanner.state['status'],'EXPIRED')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM watch_candidates').fetchone()[0],0)

    def test_http403_persists_bybit_timer_only_and_blocks_retry(self):
        exc=urllib.error.HTTPError('https://api.bybit.com/v5/market/time',403,'blocked',{},BytesIO(b''))
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect=exc
            with self.assertRaisesRegex(DataUnavailable,'BYBIT_HTTP_403'):self.client.request('/v5/market/time')
            with self.assertRaisesRegex(DataUnavailable,'BYBIT_COOLDOWN'):self.client.request('/v5/market/time')
            self.assertEqual(opener.return_value.open.call_count,1)
        self.assertGreaterEqual(self.client.gate.state()['remaining_seconds'],599)
        restarted=BybitPublic(self.store,self.stop)
        self.assertEqual(restarted.gate.state()['until_ms'],self.client.gate.state()['until_ms'])
        self.assertFalse((self.root/'binance_cooldown.json').exists())

    def test_public_get_only_and_response_clock_validation(self):
        with patch('urllib.request.build_opener') as opener:
            with self.assertRaisesRegex(ValueError,'NON_PUBLIC'):self.client.request('/v5/order/create')
            opener.assert_not_called()
            now=int(time.time()*1000)
            opener.return_value.open.return_value=Response(dict(retCode=0,time=now,result={}))
            self.client.request('/v5/market/time')
            req=opener.return_value.open.call_args.args[0]
            self.assertEqual(req.get_method(),'GET');self.assertTrue(req.full_url.startswith('https://api.bybit.com/'))
            self.assertFalse(any('key' in k.lower() or 'sign' in k.lower() for k in req.headers))
        self.client.gate.next_request=0
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.return_value=Response(dict(retCode=0,time=1,result={}))
            with self.assertRaisesRegex(DataUnavailable,'CLOCK_OR_LATENCY'):self.client.request('/v5/market/time')

    def test_retcode10006_is_not_success(self):
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.return_value=Response(dict(retCode=10006,time=NOW,result={}))
            with self.assertRaisesRegex(DataUnavailable,'10006'):self.client.request('/v5/market/time')
        self.assertGreater(self.client.gate.state()['remaining_seconds'],0)

    def test_html_is_not_a_successful_data_connection(self):
        response=Response({});response.data=b'<html>Unavailable</html>'
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.return_value=response
            with self.assertRaisesRegex(DataUnavailable,'JSONDecodeError'):self.client.request('/v5/market/time')
        self.assertGreater(self.client.gate.state()['remaining_seconds'],0)

    def test_short_candidate_is_labelled_red_bybit(self):
        fixture=self.fixture()
        def mirrored(path,**params):
            value=fixture(path,**params)
            if path.endswith('kline'):
                for row in value['result']['list']:
                    o,h,l,c=map(float,row[1:5])
                    row[1:5]=[str(210-o),str(210-l),str(210-h),str(210-c)]
            return value
        with patch.object(self.client,'request',side_effect=mirrored):self.scanner().step()
        p=json.loads(self.store.db.execute('SELECT payload FROM watch_candidates').fetchone()[0])
        self.assertEqual(p['direction'],'SHORT')
        self.assertTrue(watch_message(p).startswith('🔴 SHORT İÇİN İZLE'))

    def test_main_runner_flushes_external_outbox_while_binance_is_blocked(self):
        from types import SimpleNamespace
        fake=FakeClient(n=1);fake.blocked=True
        with patch.object(self.client,'request',side_effect=self.fixture()):self.scanner().step()
        external=SimpleNamespace(delivery=self.store,now=lambda:NOW,close=lambda:None)
        calls=[]
        def flush(sender,store,now,**kwargs):calls.append((store,kwargs.get('signals_enabled')));return 0
        with patch('ta_paper.runner.Binance',return_value=fake),patch('ta_paper.external_watch.ExternalWatch') as worker,patch.object(Telegram,'flush',flush):
            worker.return_value.start.return_value=external
            result=run(SimpleNamespace(state_root=self.tmp.name,report=False,telegram=False,once=True,external_watch='bybit'))
        self.assertEqual(result,2)  # Binance remains explicitly unavailable.
        self.assertIn((self.store,True),calls)

    def test_external_worker_reads_reference_without_binance_request(self):
        reference,stamp=reference_universe(self.root/'binance.sqlite3')
        self.assertEqual(stamp,NOW);self.assertEqual(reference['TESTUSDT'],MARKET)
        self.assertEqual(self.primary.db.execute('SELECT count(*) FROM snapshots').fetchone()[0],1)

    def test_bybit_card_delivers_via_existing_outbox(self):
        with patch.object(self.client,'request',side_effect=self.fixture()):self.scanner().step()
        telegram=Telegram(self.root);telegram.token='fixture';telegram.chat='123'
        with patch('urllib.request.build_opener') as net:
            net.return_value.open.return_value=Response(dict(ok=True,result=dict(chat={'id':123},message_id=7)))
            delivered=telegram.flush(self.store,NOW,clock=lambda:NOW)
        self.assertGreaterEqual(delivered,1)
        self.assertEqual(self.store.db.execute("SELECT state FROM outbox WHERE key LIKE 'watch:%'").fetchone()[0],'DELIVERED')

    def test_expired_card_is_not_delivered(self):
        with patch.object(self.client,'request',side_effect=self.fixture()):self.scanner().step()
        self.store.db.execute("DELETE FROM outbox WHERE key LIKE 'system:%'");self.store.db.commit()
        telegram=Telegram(self.root);telegram.token='fixture';telegram.chat='123'
        with patch('urllib.request.build_opener') as net:
            self.assertEqual(telegram.flush(self.store,NOW+6*MINUTE),0)
            net.assert_not_called()
        self.assertEqual(self.store.db.execute('SELECT state FROM outbox').fetchone()[0],'STALE')


if __name__=='__main__':unittest.main()
