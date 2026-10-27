from contextlib import redirect_stdout
from io import BytesIO, StringIO
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

from ta_paper.archive import Archive
from ta_paper.core import CFG, MINUTE, assess, closed_bars
from ta_paper.io import Binance, DataUnavailable, Store
from ta_paper.runner import STOP, scan, run, SCAN_STATE_KEY
from ta_paper.streams import LiveFeed, StreamMissing, METADATA_TTL, daily_rows, CONTROL_GROUP, SYMBOLS_PER_CONNECTION
from test_ta_paper import NOW, MARKET, series
from test_ta_paper_scan_recovery import FakeClient


class StreamTests(unittest.TestCase):
    def setUp(self):
        STOP.clear(); self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root/'research.db')
        self.client = Binance(self.root/'cooldown.json',candle_store=self.store)
        self.client.now = lambda: NOW
        self.feed = LiveFeed(self.root/'streams',self.client.gate,self.store)
        self.client.streams = self.feed
        self.feed.groups['market-0'] = dict(connected=True,acked=True,last_event=NOW)
        self.feed.groups[CONTROL_GROUP] = dict(connected=True,acked=True,last_event=NOW)
        self.feed.symbol_groups['TESTUSDT'] = 'market-0'
        self.feed.set_metadata({'symbols':[MARKET]},NOW)
        self.feed.tickers['TESTUSDT'] = dict(symbol='TESTUSDT',quoteVolume=3_000_000,closeTime=NOW)
        self.output = redirect_stdout(StringIO()); self.output.__enter__()

    def tearDown(self):
        self.feed.close(); self.store.db.close(); self.tmp.cleanup()
        self.output.__exit__(None,None,None); STOP.clear()

    def event(self,row,tf='30m',closed=True,**extra):
        return dict(e='kline',E=NOW,s='TESTUSDT',k=dict(t=row[0],T=row[6],s='TESTUSDT',i=tf,
                    o=row[1],h=row[2],l=row[3],c=row[4],v=row[5],x=closed),**extra)

    def seed(self, setups=False):
        f = FakeClient(n=1,setups=setups)
        for tf in ('30m','4h'):
            rows = f.candles('S000USDT',tf)[-119:]
            self.feed.put_bars('TESTUSDT',tf,rows,'TEST_FIXTURE',NOW)

    def test_cache_identical_assessment_to_rest_window_and_zero_network(self):
        self.seed(True)
        f = FakeClient(n=1,setups=True)
        expected = assess('TESTUSDT',f.candles('S000USDT','30m')[-119:],f.candles('S000USDT','4h')[-119:],NOW)
        with patch('urllib.request.build_opener') as net:
            actual = assess('TESTUSDT',self.client.candles('TESTUSDT','30m'),
                            self.client.candles('TESTUSDT','4h'),NOW)
            net.assert_not_called()
        self.assertEqual(actual,expected)

    def test_rest_ban_allows_verified_watch_but_never_paper_admission(self):
        self.seed(True)
        self.client.gate.block(int(time.time()*1000)+86400000,'BINANCE_HTTP_418','EXCHANGE_RETRY_TIME')
        with patch('urllib.request.build_opener') as net:
            health = scan(self.store,self.client)
            net.assert_not_called()
        self.assertTrue(health['complete'])
        self.assertEqual((health['assessed'],health['setups'],health['admitted']),(1,1,0))
        self.assertEqual(self.store.plans(),[])
        row = self.store.db.execute('SELECT payload FROM watch_candidates').fetchone()
        candidate = json.loads(row[0])
        self.assertEqual(candidate['direction'],'LONG')
        self.assertEqual(candidate['risk_reason'],'BOOK_NOT_VERIFIED')
        self.assertEqual(self.client.local_requests,0)
        scan(self.store,self.client)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM outbox WHERE key LIKE 'watch:%'").fetchone()[0],1)

    def test_gap_never_makes_a_candidate_and_does_not_veto_other_coin(self):
        self.seed(True)
        self.feed.set_metadata({'symbols':[MARKET,dict(MARKET,symbol='GAPUSDT')]},NOW)
        self.feed.tickers['GAPUSDT'] = dict(symbol='GAPUSDT',quoteVolume=4_000_000,closeTime=NOW)
        self.client.gate.block(int(time.time()*1000)+86400000,'BINANCE_HTTP_418','EXCHANGE_RETRY_TIME')
        with patch('urllib.request.build_opener') as net:
            health = scan(self.store,self.client)
            net.assert_not_called()
        self.assertFalse(health['complete'])
        self.assertEqual(health['assessed'],1)
        self.assertEqual([r[0] for r in self.store.db.execute('SELECT symbol FROM watch_candidates')],['TESTUSDT'])

    def test_disconnected_or_stale_stream_does_not_override_ban(self):
        self.seed(True)
        self.client.gate.block(int(time.time()*1000)+86400000,'BINANCE_HTTP_418','EXCHANGE_RETRY_TIME')
        for state in (dict(connected=False,acked=True,last_event=NOW),
                      dict(connected=True,acked=True,last_event=NOW-31000),
                      dict(connected=True,acked=False,last_event=NOW)):
            self.feed.groups[CONTROL_GROUP] = state
            with patch('urllib.request.build_opener') as net:
                self.assertEqual(scan(self.store,self.client)['assessed'],0)
                net.assert_not_called()

    def test_expired_metadata_is_not_made_new_by_restart_or_snapshot(self):
        self.feed.set_metadata({'symbols':[MARKET]},NOW-METADATA_TTL-1)
        self.assertFalse(self.feed.ready(NOW))
        self.feed.close()
        self.feed = LiveFeed(self.root/'streams',self.client.gate,self.store)
        self.assertEqual(self.feed.metadata_ms,NOW-METADATA_TTL-1)
        self.assertEqual(self.feed.groups,{})

    def test_failed_candle_shard_does_not_force_ticker_or_metadata_rest_requests(self):
        self.feed.groups['market-0'].update(connected=False,acked=False)
        with patch('urllib.request.build_opener') as net:
            self.assertEqual(self.client.get('/fapi/v1/ticker/24hr')[0]['symbol'],'TESTUSDT')
            self.assertEqual(self.client.get('/fapi/v1/exchangeInfo')['symbols'],[MARKET])
            self.client.sync_time()
            net.assert_not_called()

    def test_only_matching_numeric_subscription_ack_is_accepted(self):
        self.feed.groups['market-0']['acked']=False
        for value in ('subscribe',True,2,None):
            with self.assertRaisesRegex(StreamMissing,'ACK_MISMATCH'):
                self.feed.ingest('market-0',{'result':None,'id':value},NOW)
            self.assertFalse(self.feed.groups['market-0']['acked'])
        self.feed.ingest('market-0',{'result':None,'id':1},NOW)
        self.assertTrue(self.feed.groups['market-0']['acked'])

    def test_paper_admission_rechecks_fresh_market_before_order_book(self):
        self.seed(True)
        original_get = self.client.get
        calls = []
        def get(endpoint,**params):
            calls.append((endpoint,params))
            if params.get('_fresh'):
                return {'symbols':[dict(MARKET,status='SETTLING')]}
            return original_get(endpoint,**params)
        with patch.object(self.client,'get',side_effect=get),patch('urllib.request.build_opener') as net:
            health = scan(self.store,self.client)
            net.assert_not_called()
        self.assertIn(('/fapi/v1/exchangeInfo',{'_fresh':True}),calls)
        self.assertFalse(any(endpoint=='/fapi/v1/depth' for endpoint,_ in calls))
        self.assertEqual(health['admitted'],0)
        self.assertEqual(self.store.plans(),[])

    def test_archive_prepares_only_missing_completed_days_during_ban(self):
        self.client.gate.block(int(time.time()*1000)+86400000,'BINANCE_HTTP_418','EXCHANGE_RETRY_TIME')
        midnight = NOW//86400000*86400000
        self.feed.history_tails = {('TESTUSDT','30m'):midnight-2*CFG.entry_ms,
                                   ('TESTUSDT','4h'):midnight-2*CFG.context_ms}
        before = self.client.gate.state()['until_ms']
        with patch('urllib.request.build_opener') as net:
            self.feed._prime_archive(['TESTUSDT'],NOW)
            self.feed._prime_archive(['TESTUSDT'],NOW+5*MINUTE)
            net.assert_not_called()
        self.assertEqual(list(self.feed.archive_requests),[
            ('TESTUSDT','30m',midnight-CFG.entry_ms,midnight-CFG.entry_ms),
            ('TESTUSDT','4h',midnight-CFG.context_ms,midnight-CFG.context_ms)])
        self.assertEqual(self.client.gate.state()['until_ms'],before)

    def test_changed_contract_is_excluded_and_invalidation_survives_restart(self):
        self.feed.ingest('market-0',dict(e='contractInfo',E=NOW+1,s='TESTUSDT',cs='SETTLING'),NOW+1)
        self.assertEqual(self.feed.cached_metadata(NOW+1)['symbols'],[])
        self.feed.close();self.feed = LiveFeed(self.root/'streams',self.client.gate,self.store)
        self.assertEqual(self.feed.invalid_contracts,{'TESTUSDT':NOW+1})

    def test_only_closed_correct_usdm_bars_enter_durable_cache(self):
        row = series(CFG.entry_ms)[-1]
        self.feed.ingest('market-0',self.event(row,closed=False),NOW)
        self.feed.ingest('market-0',self.event(row,st=2),NOW)
        self.assertEqual(self.feed.db.execute('SELECT COUNT(*) FROM bars').fetchone()[0],0)
        bad = self.event(row);bad['k']['s']='OTHERUSDT'
        with self.assertRaisesRegex(StreamMissing,'SYMBOL_MISMATCH'):self.feed.ingest('market-0',bad,NOW)
        bad = self.event(row);bad['E']=NOW-10000
        with self.assertRaisesRegex(StreamMissing,'CLOCK_OR_LATENCY'):self.feed.ingest('market-0',bad,NOW)
        self.feed.ingest('market-0',self.event(row),NOW)
        self.assertEqual(self.feed.db.execute('SELECT source FROM bars').fetchone()[0],'BINANCE_WS')

    def test_publication_delay_and_changed_closed_bar_are_fail_closed(self):
        row = series(CFG.entry_ms)[-1]
        self.feed.ingest('market-0',self.event(row),NOW)
        changed = list(row);changed[4] += .1
        with self.assertRaisesRegex(StreamMissing,'CLOSED_CANDLE_CHANGED'):
            self.feed.ingest('market-0',self.event(changed),NOW)
        with self.assertRaisesRegex(StreamMissing,'CLOSED_CANDLE_CHANGED'):
            self.feed.candles('TESTUSDT','30m',NOW,limit=120)
        self.assertEqual(self.feed.db.execute('SELECT COUNT(*) FROM issues').fetchone()[0],1)

    def test_exact_requested_minute_range_and_gap_protection(self):
        rows = series(MINUTE,n=4,now=NOW-MINUTE)
        self.feed.put_bars('TESTUSDT','1m',rows,'TEST_FIXTURE',NOW)
        actual = self.client.candles('TESTUSDT','1m',limit=2,startTime=rows[1][0],endTime=rows[-1][6])
        self.assertEqual(actual,closed_bars(rows[1:3],MINUTE,NOW,minimum=1,require_current=False))
        self.assertEqual(self.client.candles('TESTUSDT','1m',startTime=NOW+MINUTE,endTime=NOW+2*MINUTE),[])

    def test_main_store_and_ws_merge_without_rewarming_history(self):
        rows = closed_bars(series(CFG.entry_ms),CFG.entry_ms,NOW)
        self.store.candles('TESTUSDT','30m',rows[:-1])
        self.feed.ingest('market-0',self.event(rows[-1]),NOW)
        self.assertEqual(self.feed.candles('TESTUSDT','30m',NOW),rows[-119:])
        self.feed.close();self.feed=LiveFeed(self.root/'streams',self.client.gate,self.store)
        self.assertEqual(self.feed.candles('TESTUSDT','30m',NOW),rows[-119:])

    def test_no_new_connection_before_known_ban_expires(self):
        self.client.gate.block(int(time.time()*1000)+600000,'BINANCE_HTTP_418','EXCHANGE_RETRY_TIME')
        with patch('websockets.sync.client.connect') as connect:
            thread = threading.Thread(target=self.feed._connection,args=('market-0',['TESTUSDT']))
            thread.start()
            self.feed.stop.wait(.1)
            self.feed.stop.set();thread.join(timeout=2)
            connect.assert_not_called()

    def test_full_universe_subscription_is_sharded_without_truncation(self):
        self.feed.symbol_groups.clear();self.feed.groups.clear()
        symbols = [f'X{i}USDT' for i in range(700)]
        self.feed.set_metadata({'symbols':[dict(MARKET,symbol=s) for s in symbols]},NOW)
        calls = []
        def spawn(fn,group,names):calls.append((group,names))
        with patch.object(self.feed,'_spawn',side_effect=spawn),patch('ta_paper.streams.time.time',return_value=NOW/1000):
            with patch.object(self.feed.stop,'wait',side_effect=[False,True]):self.feed._manager()
        self.assertEqual(set(s for _,names in calls for s in names),set(symbols))
        self.assertEqual(len(calls),13)  # 12 candle shards plus independent control.
        self.assertEqual([names for group,names in calls if group==CONTROL_GROUP],[[]])
        self.assertTrue(all(len(names)<=SYMBOLS_PER_CONNECTION for _,names in calls))
        self.assertEqual(len(self.feed.symbol_groups),700)

    def test_daily_archive_verifies_checksum_date_and_cache(self):
        day = NOW//86400000-1
        stamp = day*86400000
        rawcsv = 'open_time,open,high,low,close,volume,close_time\n'+f'{stamp},1,2,1,2,10,{stamp+MINUTE-1}\n'
        buffer=BytesIO()
        with zipfile.ZipFile(buffer,'w') as z:z.writestr('data.csv',rawcsv)
        raw=buffer.getvalue();checksum=hashlib.sha256(raw).hexdigest().encode()
        archive=Archive(self.root/'archive')
        with patch.object(archive,'request',side_effect=[checksum,raw]) as get:
            rows=daily_rows(archive,'TESTUSDT','1m',day)
            self.assertEqual(rows,[[stamp,1.,2.,1.,2.,10.,stamp+MINUTE-1]])
            self.assertEqual(daily_rows(archive,'TESTUSDT','1m',day),rows)
            self.assertEqual(get.call_count,2)
        archive=Archive(self.root/'bad-archive')
        with patch.object(archive,'request',side_effect=[b'0'*64,raw]):
            with self.assertRaisesRegex(ValueError,'CHECKSUM'):daily_rows(archive,'TESTUSDT','1m',day)

    def test_real_websocket_handshake_subscription_and_closed_bar_ingestion(self):
        from websockets.sync.server import serve
        row=series(CFG.entry_ms)[-1]
        requests=[]
        def handler(ws):
            requests.append(json.loads(ws.recv()))
            ws.send(json.dumps({'result':None,'id':requests[-1]['id']}))
            event=self.event(row);event['E']=int(time.time()*1000)
            # The fixture's old bar remains history, never a current signal.
            ws.send(json.dumps({'stream':'testusdt@kline_30m','data':event}))
            self.feed.stop.wait(2)
        server=serve(handler,'127.0.0.1',0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        port=server.socket.getsockname()[1]
        try:
            with patch('ta_paper.streams.URL',f'ws://127.0.0.1:{port}'):
                worker=threading.Thread(target=self.feed._connection,args=('market-0',['TESTUSDT']),daemon=True)
                worker.start()
                deadline=time.monotonic()+3
                while time.monotonic()<deadline and not self.feed.closed_count:self.feed.stop.wait(.02)
                self.assertEqual(self.feed.closed_count,1)
                self.assertEqual(requests[0]['method'],'SUBSCRIBE')
                self.assertIs(type(requests[0]['id']),int)
                self.assertNotIn('!ticker@arr',requests[0]['params'])
                self.assertIn('testusdt@kline_4h',requests[0]['params'])
                self.feed.stop.set();worker.join(timeout=3)
        finally:
            server.shutdown();thread.join(timeout=2)

    def test_control_socket_subscribes_only_to_tickers_and_contracts(self):
        from unittest.mock import MagicMock
        ws=MagicMock()
        ws.recv.side_effect=lambda **kw: (self.feed.stop.set() or json.dumps({'result':None,'id':1}))
        with patch('websockets.sync.client.connect') as connect:
            connect.return_value.__enter__.return_value=ws
            self.feed._connection(CONTROL_GROUP,[])
        request=json.loads(ws.send.call_args.args[0])
        self.assertEqual(request['params'],['!ticker@arr','!contractInfo'])

    def test_full_shard_subscription_stays_under_small_frame_budget(self):
        from unittest.mock import MagicMock
        names=[f'LONGSYMBOL{i:03d}USDT' for i in range(SYMBOLS_PER_CONNECTION)]
        ws=MagicMock()
        ws.recv.side_effect=lambda **kw: (self.feed.stop.set() or json.dumps({'result':None,'id':1}))
        with patch('websockets.sync.client.connect') as connect:
            connect.return_value.__enter__.return_value=ws
            self.feed._connection('market-0',names)
        raw=ws.send.call_args.args[0]
        self.assertLess(len(raw.encode()),8192)
        self.assertEqual(len(json.loads(raw)['params']),len(names)*3)


if __name__ == '__main__':unittest.main()
