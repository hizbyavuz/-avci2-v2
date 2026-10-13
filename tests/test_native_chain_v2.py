import copy
import unittest
from unittest.mock import patch
from long_short_native_derivatives import validate,ENDPOINTS,SOURCE
from long_short_outcomes_v2 import label_path

NOW=1791889036000
SYMBOL='BTCUSDT'

def fixture():
    end=NOW//300000*300000
    return dict(source=SOURCE,symbol=SYMBOL,observed_ms=NOW,endpoints=list(ENDPOINTS),
        market=dict(symbol=SYMBOL,status='TRADING',contractType='PERPETUAL',quoteAsset='USDT',marginAsset='USDT'),
        oi=dict(symbol=SYMBOL,time=NOW,openInterest='100'),
        oi_history=[dict(symbol=SYMBOL,timestamp=end-(12-i)*300000,sumOpenInterest=str(100+i)) for i in range(13)],
        long_short=[dict(symbol=SYMBOL,timestamp=end,longShortRatio='1.1749')],
        mark=dict(symbol=SYMBOL,time=NOW,lastFundingRate='0.00007686',nextFundingTime=NOW+3600000,markPrice='100'))

class NativeSnapshotTests(unittest.TestCase):
    def test_units_and_one_hour_window(self):
        x=validate(fixture(),SYMBOL,NOW)
        self.assertAlmostEqual(x['oi_change_1h'],12)
        self.assertAlmostEqual(x['funding_pct'],0.007686)
        self.assertEqual(x['oi_unit'],'BASE_ASSET')
        self.assertEqual(x['long_short_definition'],'BINANCE_GLOBAL_ACCOUNT_RATIO')

    def test_market_identity_required(self):
        for key,value in [('symbol','ETHUSDT'),('status','BREAK'),('contractType','CURRENT_QUARTER'),('marginAsset','BTC')]:
            p=fixture();p['market'][key]=value
            with self.assertRaises(ValueError):validate(p,SYMBOL,NOW)

    def test_stale_current_or_future_rejected(self):
        for field in ['oi','mark']:
            for age in [180001,-3000]:
                p=fixture();p[field]['time']=NOW-age
                with self.assertRaises(ValueError):validate(p,SYMBOL,NOW)

    def test_history_gaps_and_wrong_units_not_substituted(self):
        p=fixture();p['oi_history'][5]['timestamp']+=1
        with self.assertRaises(ValueError):validate(p,SYMBOL,NOW)
        p=fixture();del p['oi_history'][0]['sumOpenInterest'];p['oi_history'][0]['sumOpenInterestValue']='100000'
        with self.assertRaises(ValueError):validate(p,SYMBOL,NOW)

    def test_missing_long_short_not_neutral_one(self):
        p=fixture();p['long_short']=[]
        with self.assertRaises(ValueError):validate(p,SYMBOL,NOW)

    def test_nan_and_wrong_source_rejected(self):
        p=fixture();p['mark']['lastFundingRate']='nan'
        with self.assertRaises(ValueError):validate(p,SYMBOL,NOW)
        p=fixture();p['source']='BINANCE_SPOT'
        with self.assertRaises(ValueError):validate(p,SYMBOL,NOW)

    def test_four_factor_gate_consumes_same_snapshot(self):
        from long_short_binance_derivative_gate import assess
        e=validate(fixture(),SYMBOL,NOW);e.update(taker_fresh=True,taker_coverage_ready=True)
        self.assertTrue(assess(e,funding_pct=e['funding_pct'],funding_source=SOURCE,
            taker_ratio=1.2,taker_source='BINANCE_UM_FUTURES_KLINE')['ready'])
        e['market_verified']=False
        self.assertFalse(assess(e,funding_pct=e['funding_pct'],funding_source=SOURCE,
            taker_ratio=1.2,taker_source='BINANCE_UM_FUTURES_KLINE')['ready'])

    def test_live_gate_uses_provider_not_four_coin_audit(self):
        import long_short_live_pool as lp
        with patch.dict('os.environ',{'LS_NATIVE_CHAIN_V2':'1'}),patch('long_short_native_derivatives.evidence',return_value={
            'ready':True,'missing':[],'source':SOURCE,'observed_ms':NOW}):
            self.assertTrue(lp.binance_four_factor_confirmation_gate(SYMBOL)['qualified'])

    def test_observation_default_preserved_and_v2_authorization(self):
        import long_short_live_pool as lp
        early={'live_price_source':'BINANCE_FUTURES','native_futures_observation_only':False,'rolling_24h_stats_verified':True}
        self.assertTrue(lp.futures_signal_authorization(early,{'qualified':True})['qualified'])
        early['native_futures_observation_only']=True
        self.assertFalse(lp.futures_signal_authorization(early,{'qualified':True})['qualified'])
        early['native_futures_observation_only']=False;early['live_price_source']='BINANCE_SPOT'
        self.assertFalse(lp.futures_signal_authorization(early,{'qualified':True})['qualified'])

class V2MathTests(unittest.TestCase):
    def test_real_cost_in_denominator(self):
        import long_short_live_pool as lp
        row={'direction':'LONG','data_mode':'BINANCE_FUTURES','derivatives_provider':'BINANCE_FUTURES',
            'invalidation':99,'target1':101.31}
        book={'available':True,'source':'BINANCE_FUTURES_BOOK','notional_usdt':250,
            'costs':{'250':{'buy_bps':10,'sell_bps':10}}}
        with patch.dict('os.environ',{'LS_NATIVE_CHAIN_V2':'1'}):
            x=lp.live_execution_gate(row,100,book)
        self.assertFalse(x['qualified'])
        self.assertAlmostEqual(x['net_t1_r'],1.01/1.3)

    def test_gap_stop_uses_executable_open(self):
        x=label_path('LONG',100,99,103,[[60000,97,100,96,98]],0.4)
        self.assertEqual(x['exit_price'],97)
        self.assertAlmostEqual(x['net_return_pct'],-3.4)

    def test_same_candle_stop_first(self):
        x=label_path('SHORT',100,102,97,[[60000,100,103,96,100]],0.3)
        self.assertEqual(x['result'],'STOP')

    def test_actual_cost_and_funding_applied(self):
        x=label_path('LONG',100,99,103,[[60000,100,104,100,103]],0.5,0.2)
        self.assertEqual(x['result'],'TP1')
        self.assertAlmostEqual(x['net_return_pct'],2.3)

    def test_empty_path_never_fabricates_loss(self):
        with self.assertRaises(ValueError):label_path('LONG',100,99,103,[],0.3)
