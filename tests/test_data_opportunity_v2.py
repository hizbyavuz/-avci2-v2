import json
import os
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import long_short_data_opportunity_v2 as m

def evidence():
    return dict(source='BINANCE_UM_FUTURES_REST',market_verified=True,oi_unit='BASE_ASSET',
        oi_change_1h=2,oi_fresh=True,funding_pct=0,funding_fresh=True,
        taker_ratio=None,taker_fresh=False,taker_coverage_ready=False,
        long_short_ratio=1.2,ls_fresh=True)

class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'LS_STATE_DIR':self.tmp.name})
        self.env.start()
    def tearDown(self):
        self.env.stop();self.tmp.cleanup()
    def test_exact_three_and_verified_units(self):
        self.assertEqual(m.partial_fields(evidence()),['taker_ratio'])
        e=evidence();e['funding_fresh']=False
        self.assertIsNone(m.partial_fields(e))
        e=evidence();e['source']='BINANCE_SPOT'
        self.assertIsNone(m.partial_fields(e))
        e=evidence();e['oi_unit']='USD'
        self.assertIsNone(m.partial_fields(e))
    def test_nonfinite_and_wrong_taker_source_are_missing(self):
        e=evidence();e.update(taker_ratio=1.2,taker_fresh=True,taker_coverage_ready=True,taker_source='BINANCE_SPOT')
        self.assertEqual(m.partial_fields(e),['taker_ratio'])
        e['funding_pct']=float('nan')
        self.assertIsNone(m.partial_fields(e))
    def test_episode_samples_and_recovery_preserved(self):
        with patch.object(m,'current_price',return_value=100):
            m.blocked('XUSDT','taker_ratio',100000)
            m.blocked('XUSDT','taker_ratio',160000)
            a=SimpleNamespace(symbol='XUSDT',long_score=50,short_score=0,payload={
                'native_chain_v2':True,'derivatives_ready':True,'direction_engine':{'direction':'LONG'}})
            m.record_analysis(a,now_ms=220000)
        with m.connect() as c:
            self.assertEqual(c.execute('SELECT started_ms,recovered_ms FROM episodes').fetchall(),[(100000,220000)])
            self.assertEqual(c.execute('SELECT count(*) FROM samples').fetchone()[0],2)
            self.assertEqual(c.execute('SELECT cohort FROM observations ORDER BY id').fetchall(),[('DATA_BLOCKED',),('FULL_4_OF_4',)])
        c.close()
    def test_partial_is_separate_and_not_complete(self):
        a=SimpleNamespace(symbol='XUSDT',long_score=50,short_score=0,payload={
            'native_chain_v2':True,'partial_research_only':True,'derivatives_ready':False,
            'native_derivative_evidence':evidence(),'direction_engine':{'direction':'LONG'},
            'actionable_liquidity_ok':True,'structure_gate':{'qualified_precheck':True},'t15':{'atr_pct':1}})
        with patch.object(m,'current_price',return_value=100):
            m.record_analysis(a,partial=True,now_ms=100000)
            m.record_analysis(a,partial=False,now_ms=100000)
        with m.connect() as c:
            rows=c.execute('SELECT cohort,payload FROM observations').fetchall()
            self.assertEqual(len(rows),1);self.assertEqual(rows[0][0],'PARTIAL_3_OF_4')
            self.assertTrue(json.loads(rows[0][1])['research_only'])
            self.assertTrue(json.loads(rows[0][1])['eligible_observation'])
        c.close()
    def test_missing_reference_is_not_fabricated(self):
        self.assertIsNone(m.current_price('XUSDT',100000))
        with self.assertRaises(ValueError):m.label(None,'LONG',[],120000,15)
    def test_reference_requires_fresh_native_futures(self):
        path=os.path.join(self.tmp.name,'binance_futures_kline_shadow.db')
        with sqlite3.connect(path) as c:
            c.execute('CREATE TABLE futures_last_price(symbol,price,event_ms,received_ms,source)')
            c.execute('INSERT INTO futures_last_price VALUES(?,?,?,?,?)',('XUSDT',100,100000,100000,'BINANCE_UM_FUTURES_WEBSOCKET'))
        self.assertEqual(m.current_price('XUSDT',120000),100)
        self.assertIsNone(m.current_price('XUSDT',140000))
        with sqlite3.connect(path) as c:c.execute("UPDATE futures_last_price SET source='BINANCE_SPOT'")
        self.assertIsNone(m.current_price('XUSDT',120000))
    def test_shortlist_departure_retains_recent_research_coin(self):
        with patch.object(m,'current_price',return_value=100):m.observe('XUSDT','DATA_BLOCKED','NONE',{},100000)
        self.assertEqual(m.pending_symbols(100000+180*60000),['XUSDT'])
        self.assertEqual(m.pending_symbols(100000+186*60000),[])
    def test_contiguous_future_and_both_directions(self):
        bars=[[120000+i*60000,100,104,98,103] for i in range(15)]
        a=m.label(100,'LONG',bars,120000,15)
        b=m.label(100,'SHORT',bars,120000,15)
        self.assertAlmostEqual(a['directional_return_pct'],3)
        self.assertAlmostEqual(b['directional_return_pct'],-3)
        self.assertEqual(a['hits']['up_3pct_first_full_minute'],1)
        self.assertIsNone(a['hits']['up_5pct_first_full_minute'])
        with self.assertRaises(ValueError):m.label(100,'LONG',bars[1:],120000,15)
    def test_resolver_waits_and_preserves_unresolved(self):
        with patch.object(m,'current_price',return_value=100):
            m.observe('XUSDT','PARTIAL_3_OF_4','LONG',{},100000)
        with patch('long_short_live_pool.native_futures_path_bars',side_effect=RuntimeError('missing candle')):
            m.resolve(130000)
            with m.connect() as c:self.assertEqual(c.execute('SELECT count(*) FROM forward_labels').fetchone()[0],0)
            c.close()
            m.resolve(120000+15*60000)
        with m.connect() as c:
            self.assertEqual(c.execute('SELECT state FROM forward_labels').fetchone()[0],'UNRESOLVED')
            self.assertEqual(c.execute('SELECT count(*) FROM observations').fetchone()[0],1)
        c.close()
    def test_partial_cannot_enter_live_watchlist(self):
        import long_short_live_pool as live
        path=os.path.join(self.tmp.name,'analyst.db')
        with sqlite3.connect(path) as c:
            c.execute('CREATE TABLE analyses(symbol,status,long_score,short_score,confidence,price,payload_json,scan_time_utc)')
            c.execute('INSERT INTO analyses VALUES(?,?,?,?,?,?,?,?)',('XUSDT','WAIT',60,0,60,100,json.dumps({'native_chain_v2':True,'partial_research_only':True}),'2026-10-13T10:00:00+00:00'))
        with patch.object(live,'ANALYST_DB',path):self.assertEqual(live.load_watchlist(),[])

    def test_actual_partial_score_stays_no_trade_and_missing_stays_none(self):
        import long_short_analyst as a
        raw=[[1790000000000+i*60000,100+i*.01,100+i*.01+.1,100+i*.01-.1,
              100+i*.01+.03,100,1790000059999+i*60000,10000,100,55,5500,0] for i in range(230)]
        bars=a.parse_klines(raw)
        e=evidence();e.update(symbol='BTCUSDT',ready=False,missing=['taker_ratio'],oi_now=100)
        with patch.dict(os.environ,{'LS_NATIVE_CHAIN_V2':'1','LS_BINANCE_FUTURES_ONLY':'1'}),\
             patch.object(a,'DATA_MODE','BINANCE_FUTURES'),\
             patch.object(a,'fetch_klines',return_value=bars),\
             patch.object(a,'fetch_htf_cached',return_value=bars),\
             patch.object(a,'build_htf_gate',return_value={}),\
             patch.object(a,'fetch_depth_metrics',return_value={'imbalance':0}),\
             patch.object(a,'spot_delta_proxy',return_value={}),\
             patch('long_short_gate_audit.record_setup_gates'),\
             patch('requests.get',side_effect=AssertionError('research must not use external data')):
            x=a.score_symbol('BTCUSDT','UP',_partial_research=True,_research_evidence=e)
            full=dict(e,ready=True,missing=[],taker_ratio=1.2,taker_fresh=True,taker_coverage_ready=True,taker_source='BINANCE_UM_FUTURES_WEBSOCKET')
            with patch('long_short_native_derivatives.evidence',return_value=full):
                y=a.score_symbol('BTCUSDT','UP')
            with patch.dict(os.environ,{'LS_DATA_OPPORTUNITY_V2':'1'}),\
                 patch('long_short_native_derivatives.evidence',return_value=e),\
                 patch.object(m,'record_analysis') as record,patch.object(m,'blocked'):
                with self.assertRaisesRegex(RuntimeError,'derivative evidence incomplete'):
                    a.score_symbol('BTCUSDT','UP')
                self.assertEqual(record.call_count,1)
                self.assertTrue(record.call_args.kwargs['partial'])
            self.assertEqual(y.payload['score_components']['derivatives']['long']-x.payload['score_components']['derivatives']['long'],7)
        self.assertEqual(x.status,'NO_TRADE')
        self.assertFalse(x.payload['direction_engine']['eligible'])
        self.assertIsNone(x.payload['taker_ratio'])
        self.assertEqual(x.payload['derivatives_v3_core_coverage'],.75)

if __name__=='__main__':unittest.main()
