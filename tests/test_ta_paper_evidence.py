from contextlib import redirect_stdout
import hashlib
from io import StringIO
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from ta_paper.core import CFG
from ta_paper.evidence import Evidence, MINUTE, DAY, path_result, readonly, collect_funding, dumps
from ta_paper.evidence_worker import Feed, cooldown
from ta_paper.feature_archive import candle_features, hypothesis_results, FeatureArchive
from ta_paper.io import Store

START=1_790_000_040_000
PLAN=dict(symbol='TESTUSDT',direction='LONG',stop=99.,target=102.,entry_limit=100.5,
          reference_entry=100.,candle_ms=START-30*MINUTE,observed_ms=START-40_000,
          source_venue='BYBIT',config_hash=CFG.fingerprint)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.e=Evidence(self.root/'observations.sqlite3')
    def tearDown(self):self.e.db.close();self.tmp.cleanup()
    def bar(self,t=START,o=100,h=100.3,l=99.5,c=100):return [t,o,h,l,c,10]

    def test_frozen_strategy_and_original_replay_hashes(self):
        root=Path(__file__).resolve().parents[1]
        protocol=json.loads((root/'research/ta_paper_observation_v1/protocol.json').read_text())
        self.assertEqual(CFG.fingerprint,protocol['config_hash'])
        for name,expected in protocol['frozen_files_sha256'].items():
            self.assertEqual(hashlib.sha256((root/'ta_paper'/name).read_bytes()).hexdigest(),expected,name)

    def test_post_notification_anchor_excludes_earlier_winner(self):
        rows=[self.bar(START-MINUTE,h=105),self.bar()]
        result=path_result(PLAN,rows,START,START+MINUTE,START+MINUTE+2000)
        self.assertEqual(result['status'],'TRACKING');self.assertIsNone(result['first_barrier'])
        self.assertIsNone(result['net_r'])

    def test_both_barriers_same_minute_stop_first(self):
        r=path_result(PLAN,[self.bar(h=103,l=98)],START,START+MINUTE,START+MINUTE+2000)
        self.assertEqual(r['first_barrier'],'STOP');self.assertTrue(r['same_minute_ambiguous'])
        self.assertLess(r['model_r_before_funding'],-1)
        self.assertLess(r['model_r_before_funding_1_5'],r['model_r_before_funding'])

    def test_gap_after_entry_uses_open_beyond_stop(self):
        r=path_result(PLAN,[self.bar(),self.bar(START+MINUTE,o=98,h=98.5,l=97,c=98)],
                      START,START+2*MINUTE,START+2*MINUTE+2000)
        self.assertEqual(r['gross_reference_r'],-2)

    def test_missing_minute_does_not_count_later_target(self):
        r=path_result(PLAN,[self.bar(),self.bar(START+2*MINUTE,h=103)],START,START+3*MINUTE,START+3*MINUTE+2000)
        self.assertEqual(r['status'],'UNRESOLVED_DATA');self.assertIsNone(r['first_barrier'])

    def test_missing_entry_and_chased_entry_separate(self):
        r=path_result(PLAN,[],START,START+MINUTE,START+MINUTE+2000)
        self.assertEqual(r['status'],'UNRESOLVED_DATA')
        r=path_result(PLAN,[self.bar(o=101,h=102,l=100.6,c=101)],START,START+MINUTE,START+MINUTE+2000)
        self.assertEqual(r['status'],'NO_MODEL_ENTRY')

    def test_short_target_and_directional_checkpoints(self):
        p=dict(PLAN,direction='SHORT',stop=101,target=98,entry_limit=99.5)
        rows=[self.bar(START+i*MINUTE,h=100.3,l=98,c=99) for i in range(60)]
        r=path_result(p,rows,START,START+60*MINUTE,START+60*MINUTE+2000)
        self.assertEqual(r['first_barrier'],'TARGET')
        self.assertAlmostEqual(r['checkpoints']['60']['directional_return'],.01)

    def test_future_received_ticker_is_not_asof_evidence(self):
        self.e.sample('TESTUSDT',START,START+1,{'openInterest':100})
        self.assertIsNone(self.e.asof('TESTUSDT',START))
        self.assertEqual(self.e.asof('TESTUSDT',START+2)['openInterest'],100)

    def test_missing_features_are_not_zero_or_passed(self):
        f=self.e.market_features('TESTUSDT',START,START-30*MINUTE)
        self.assertIsNone(f['oi_change_4h']);self.assertIsNone(f['taker_buy_share_30m'])
        self.assertEqual(hypothesis_results(f,'LONG'),dict(H1=None,H2=None,H3=None,H4=None))

    def test_flow_requires_every_closed_minute_and_no_future_receipt(self):
        for i in range(30):
            at=START+i*MINUTE
            self.e.db.execute('INSERT INTO flows VALUES(?,?,?,?,?,?,?)',('TESTUSDT',at,60,40,10,1,at+MINUTE))
        f=self.e.market_features('TESTUSDT',START+30*MINUTE,START)
        self.assertAlmostEqual(f['taker_buy_share_30m'],.6)
        self.e.db.execute("UPDATE flows SET complete=0 WHERE ms=?",(START+MINUTE,))
        self.assertIsNone(self.e.market_features('TESTUSDT',START+30*MINUTE,START)['taker_buy_share_30m'])

    def test_delivery_registration_idempotent_and_source_readonly(self):
        source=Store(self.root/'source.sqlite3')
        try:
            p=dict(PLAN,id='BYBIT:test')
            source.db.execute('INSERT INTO watch_candidates VALUES(?,?,?,?,?,?)',
                              ('BYBIT:test','TESTUSDT',START-45_000,START+MINUTE,'ACTIVE',dumps(p)))
            source.db.execute("INSERT INTO outbox(key,ms,text,state,message_id,delivered_at_ms) VALUES(?,?,?,?,?,?)",
                              ('watch:BYBIT:test',START-45_000,'test','DELIVERED','1',START-10_000))
            source.db.commit()
            with readonly(self.root/'source.sqlite3') as read:
                self.assertEqual(self.e.register(read,START),1)
                self.assertEqual(self.e.register(read,START),0)
                with self.assertRaises(Exception):read.execute('DELETE FROM watch_candidates')
            row=self.e.db.execute('SELECT * FROM candidates').fetchone()
            self.assertEqual(row['anchor_ms'],START)
            self.assertEqual(source.db.execute('SELECT count(*) FROM plans').fetchone()[0],0)
            self.assertEqual(source.db.execute('SELECT count(*) FROM watch_candidates').fetchone()[0],1)
        finally:source.db.close()

    def test_old_ack_without_timestamp_not_in_new_cohort(self):
        source=Store(self.root/'source.sqlite3')
        try:
            source.db.execute('INSERT INTO watch_candidates VALUES(?,?,?,?,?,?)',
                              ('old','TESTUSDT',START,START+MINUTE,'ACTIVE',dumps(PLAN)))
            source.queue('watch:old','old',START)
            source.db.execute("UPDATE outbox SET state='DELIVERED'");source.db.commit()
            with readonly(self.root/'source.sqlite3') as read:self.assertEqual(self.e.register(read,START),0)
        finally:source.db.close()

    def test_taker_archive_preserves_quote_units_and_missing_is_error(self):
        raw=[START,100,102,99,100,10,START+MINUTE-1,1000,20,6,600,0]
        self.assertAlmostEqual(candle_features(raw)['taker_buy_share'],.6)
        self.assertEqual(candle_features(raw)['taker_sell_quote'],400)
        with self.assertRaises(ValueError):candle_features(raw[:8])
        raw[10]=1001
        with self.assertRaises(ValueError):candle_features(raw)

    def test_each_hypothesis_independent_and_oi_alone_not_direction(self):
        f=dict(taker_buy_share_30m=.6,oi_change_4h=.1,return_4h=-.02,
               funding_percentile_30d=.95,relative_return_3d=.03)
        self.assertEqual(hypothesis_results(f,'LONG'),dict(H1=True,H2=False,H3=False,H4=True))

    def test_partial_and_open_candles_not_outcomes(self):
        feed=Feed(self.e);feed.connected(['TESTUSDT'],START)
        feed.handle(dict(topic='kline.1.TESTUSDT',ts=START+MINUTE,data=[dict(
            confirm=False,interval='1',start=START,end=START+MINUTE-1,open='100',high='102',low='99',close='101',volume='10')]),START+MINUTE)
        self.assertEqual(self.e.db.execute('SELECT count(*) FROM bars').fetchone()[0],0)

    def test_flow_duplicate_id_same_seq_and_reconnect(self):
        feed=Feed(self.e);feed.connected(['TESTUSDT'],START)
        def message(ids):return dict(topic='publicTrade.TESTUSDT',ts=START+1000,data=[dict(
            T=START+1000,s='TESTUSDT',S='Buy',p='100',v='1',i=i,seq=1) for i in ids])
        feed.handle(message(['a','a','b']),START+1000)
        feed.flush(START+MINUTE+2000)
        row=self.e.db.execute('SELECT * FROM flows WHERE ms=?',(START,)).fetchone()
        self.assertEqual(row['trades'],2);self.assertEqual(row['buy_quote'],200);self.assertEqual(row['complete'],1)
        feed.reset(START+MINUTE+3000);feed.connected(['TESTUSDT'],START+MINUTE+4000)
        feed.flush(START+2*MINUTE+2000)
        row=self.e.db.execute('SELECT * FROM flows WHERE ms=?',(START+MINUTE,)).fetchone()
        self.assertEqual(row['complete'],0)

    def test_late_trade_invalidates_finalized_flow(self):
        feed=Feed(self.e);feed.connected(['TESTUSDT'],START);feed.flush(START+MINUTE+2000)
        feed.handle(dict(topic='publicTrade.TESTUSDT',ts=START+MINUTE+2500,data=[dict(
            T=START+MINUTE-1,s='TESTUSDT',S='Sell',p='100',v='1',i='late')]),START+MINUTE+2500)
        self.assertEqual(self.e.db.execute('SELECT complete FROM flows WHERE ms=?',(START,)).fetchone()[0],0)

    def test_bad_or_active_cooldown_blocks_observer_connections(self):
        path=self.root/'gate.json';path.write_text('{bad')
        self.assertTrue(cooldown(path,START))
        path.write_text(dumps(dict(until_ms=START+MINUTE)))
        self.assertTrue(cooldown(path,START));self.assertFalse(cooldown(path,START+2*MINUTE))

    def test_funding_history_uses_scanner_gate_and_five_minute_budget(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        client=SimpleNamespace(now=lambda:START+10*MINUTE,gate=Mock(),request=Mock())
        client.gate.state.return_value={'remaining_seconds':60}
        self.assertEqual(collect_funding(self.root/'observations.sqlite3',client),0)
        client.request.assert_not_called()
        result=dict(first_barrier='TARGET',exit_ms=START+MINUTE)
        self.e.db.execute('INSERT INTO candidates VALUES(?,?,?,?,?,?,?)',
                          ('x','TESTUSDT',START,START,dumps(PLAN),'{}',dumps(result)));self.e.db.commit()
        client.gate.state.return_value={'remaining_seconds':0}
        client.request.return_value=dict(result=dict(category='linear',list=[dict(
            symbol='TESTUSDT',fundingRateTimestamp=str(START),fundingRate='0.001')]))
        self.assertEqual(collect_funding(self.root/'observations.sqlite3',client),1)
        self.assertEqual(collect_funding(self.root/'observations.sqlite3',client),0)
        self.assertEqual(client.request.call_count,1)
        self.assertEqual(client.request.call_args.args[0],'/v5/market/funding/history')

    def test_settled_funding_cost_does_not_become_zero_when_mark_missing(self):
        self.e.db.execute('INSERT INTO candidates VALUES(?,?,?,?,?,?,?)',
            ('x','TESTUSDT',START,START,dumps(PLAN),dumps(dict(missing=[])),'{}'))
        self.e.bar('TESTUSDT',self.bar(),START+MINUTE)
        self.e.bar('TESTUSDT',self.bar(START+MINUTE),START+2*MINUTE)
        self.e.bar('TESTUSDT',self.bar(START+2*MINUTE,h=103),START+3*MINUTE)
        self.e.db.execute('INSERT INTO funding VALUES(?,?,?,?)',('TESTUSDT',START+MINUTE,.001,START+3*MINUTE))
        self.e.db.execute('INSERT INTO meta VALUES(?,?)',('funding:x',str(START+3*MINUTE)))
        self.e.evaluate(START+3*MINUTE+2000)
        r=json.loads(self.e.db.execute('SELECT result FROM candidates').fetchone()[0])
        self.assertNotIn('model_net_r',r)
        self.e.sample('TESTUSDT',START+MINUTE-1,START+MINUTE-1,dict(markPrice=100))
        self.e.evaluate(START+3*MINUTE+2000)
        r=json.loads(self.e.db.execute('SELECT result FROM candidates').fetchone()[0])
        self.assertAlmostEqual(r['model_r_before_funding']-r['model_net_r'],.1)
        self.assertIsNone(r['net_r'])

    def test_ws_worker_real_local_handshake_does_not_mutate_signal_database(self):
        from websockets.sync.server import serve
        from ta_paper.evidence_worker import run
        source=Store(self.root/'research.sqlite3')
        source.setting('bybit_watch_scan_v1',dumps(dict(markets={'TESTUSDT':{}},
                                                      tickers={'TESTUSDT':dict(quoteVolume=3_000_000)})))
        stop=threading.Event();requests=[]
        at=int(time.time()*1000)//MINUTE*MINUTE-MINUTE
        def handler(ws):
            request=json.loads(ws.recv());requests.append(request)
            ws.send(dumps(dict(op='subscribe',success=True,req_id=request['req_id'])))
            ws.send(dumps(dict(topic='tickers.TESTUSDT',type='snapshot',ts=int(time.time()*1000),data=dict(
                symbol='TESTUSDT',lastPrice='100',markPrice='100',indexPrice='100',openInterest='1000',fundingRate='0.001'))))
            ws.send(dumps(dict(topic='kline.1.TESTUSDT',ts=int(time.time()*1000),data=[dict(
                confirm=True,interval='1',start=at,end=at+MINUTE-1,open='100',high='101',low='99',close='100',volume='10')])))
            stop.wait(3)
        server=serve(handler,'127.0.0.1',0)
        server_thread=threading.Thread(target=server.serve_forever,daemon=True);server_thread.start()
        try:
            with patch('ta_paper.evidence_worker.URL',f'ws://127.0.0.1:{server.socket.getsockname()[1]}'),redirect_stdout(StringIO()):
                worker=threading.Thread(target=run,args=(self.root,stop),daemon=True);worker.start()
                deadline=time.monotonic()+3
                while time.monotonic()<deadline:
                    if self.e.db.execute('SELECT count(*) FROM bars').fetchone()[0]:break
                    stop.wait(.02)
                self.assertEqual(self.e.db.execute('SELECT count(*) FROM bars').fetchone()[0],1)
                self.assertEqual(source.db.execute('SELECT count(*) FROM plans').fetchone()[0],0)
                self.assertEqual(source.db.execute('SELECT count(*) FROM outbox').fetchone()[0],0)
                self.assertIn('publicTrade.TESTUSDT',requests[0]['args'])
                self.assertTrue(all(r['op'] in ('subscribe','ping') for r in requests))
                stop.set();worker.join(timeout=4)
                self.assertFalse(worker.is_alive())
        finally:
            stop.set();server.shutdown();server_thread.join(timeout=3);source.db.close()

    def test_metrics_parser_actual_schema_and_checksum(self):
        import io,zipfile
        raw=('create_time,symbol,sum_open_interest,sum_open_interest_value\n'
             '2025-10-01 00:00:00,BTCUSDT,88419.973,10092485610.1498\n'
             '2025-10-01 00:05:00,BTCUSDT,88376.387,10091505203.4786\n')
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w') as z:z.writestr('data.csv',raw)
        blob=buffer.getvalue();checksum=hashlib.sha256(blob).hexdigest().encode()
        archive=FeatureArchive(self.root/'archive')
        with patch.object(archive,'request',side_effect=[checksum,blob]) as request:
            rows=archive.metrics_rows('BTCUSDT','2025-10-01')
            self.assertEqual(rows[1]['event_ms']-rows[0]['event_ms'],5*MINUTE)
            self.assertFalse(rows[0]['publication_time_verified'])
            self.assertEqual(rows[0]['available_ms_assumption']-rows[0]['event_ms'],6*MINUTE)
            archive.metrics_rows('BTCUSDT','2025-10-01');self.assertEqual(request.call_count,2)

    def test_binance_passive_taker_capture_does_not_change_candles(self):
        source=Store(self.root/'source.sqlite3')
        try:
            raw=[START,100,102,99,100,10,START+MINUTE-1,1000,20,6,600]
            source.passive_kline_features('TESTUSDT','30m',[raw],START+MINUTE+2000)
            self.assertEqual(source.db.execute('SELECT count(*) FROM passive_kline_features').fetchone()[0],1)
            self.assertEqual(source.db.execute('SELECT count(*) FROM candles').fetchone()[0],0)
            self.assertEqual(source.db.execute('SELECT count(*) FROM plans').fetchone()[0],0)
        finally:source.db.close()

    def test_completed_research_reused_only_for_verified_config_and_protocol(self):
        from ta_paper.research_cache import completed_folder,VERIFIED_SOURCES,PROTOCOL_SHA
        sha=VERIFIED_SOURCES[0];folder=self.root/sha[:16];folder.mkdir()
        (folder/'job_status.json').write_text(dumps(dict(status='COMPLETE',provenance=dict(source_sha256=sha))))
        report=dict(config_hash=CFG.fingerprint,run_identity=dict(protocol_sha256=PROTOCOL_SHA),
                    provenance=dict(source_sha256=sha))
        (folder/'report.json').write_text(dumps(report))
        self.assertEqual(completed_folder(self.root,'a'*64,CFG.fingerprint),folder)
        report['config_hash']='different';(folder/'report.json').write_text(dumps(report))
        self.assertEqual(completed_folder(self.root,'a'*64,CFG.fingerprint),self.root/('a'*16))

    def test_bybit_scan_summaries_only_notify_health_changes(self):
        from ta_paper.external_watch import WatchScanner
        from types import SimpleNamespace
        source=Store(self.root/'source.sqlite3')
        try:
            scanner=WatchScanner(source,SimpleNamespace(now=lambda:START),'unused',threading.Event())
            with redirect_stdout(StringIO()):
                for slot in (1,2):
                    scanner.state=dict(slot=slot,status='COMPLETE',done={},markets={},errors={})
                    scanner.report()
            self.assertEqual(source.db.execute('SELECT count(*) FROM outbox').fetchone()[0],1)
            self.assertEqual(source.db.execute("SELECT count(*) FROM events WHERE kind='EXTERNAL_SCAN'").fetchone()[0],2)
        finally:source.db.close()

    def test_known_stop_stays_stop_if_later_observation_path_has_gap(self):
        r=path_result(PLAN,[self.bar(l=98)],START,START+3*MINUTE,START+3*MINUTE+2000)
        self.assertEqual(r['status'],'STOP')
        self.assertEqual(r['path_status'],'MISSING_DATA')

    def test_ambiguous_funding_time_is_not_used_to_claim_net_profit(self):
        self.e.db.execute('INSERT INTO candidates VALUES(?,?,?,?,?,?,?)',
            ('x','TESTUSDT',START,START,dumps(PLAN),dumps(dict(missing=[])),'{}'))
        self.e.bar('TESTUSDT',self.bar(h=103),START+MINUTE)
        self.e.db.execute('INSERT INTO funding VALUES(?,?,?,?)',('TESTUSDT',START,-.001,START+MINUTE))
        self.e.db.execute('INSERT INTO meta VALUES(?,?)',('funding:x',str(START+MINUTE)))
        self.e.sample('TESTUSDT',START-1,START-1,dict(markPrice=100))
        with redirect_stdout(StringIO()):self.e.evaluate(START+MINUTE+2000)
        r=json.loads(self.e.db.execute('SELECT result FROM candidates').fetchone()[0])
        self.assertTrue(r['funding_timing_ambiguous']);self.assertNotIn('model_net_r',r)


if __name__=='__main__':unittest.main()
