import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import long_short_major_context as context
import long_short_major_context_report as reporting
import long_short_major_move_store as store
import long_short_native_derivatives as native
from long_short_futures_kline_shadow import initialize
from tests import test_major_move as fixtures

NOW=fixtures.NOW

END=(NOW-1000)//300000*300000
SYMBOL='TESTUSDT'


def payload():
    return dict(source=native.SOURCE,symbol=SYMBOL,observed_ms=NOW,endpoints=list(native.ENDPOINTS),
        market=dict(symbol=SYMBOL,status='TRADING',contractType='PERPETUAL',quoteAsset='USDT',marginAsset='USDT'),
        oi=dict(symbol=SYMBOL,time=NOW,openInterest='112'),
        oi_history=[dict(symbol=SYMBOL,timestamp=END-(12-i)*300000,sumOpenInterest=str(100+i)) for i in range(13)],
        long_short=[dict(symbol=SYMBOL,timestamp=END,longShortRatio='1.2')],
        mark=dict(symbol=SYMBOL,time=NOW,lastFundingRate='0.0001',nextFundingTime=NOW+3600000,markPrice='103'))


def flow(share=.6,change=3.):
    return dict(status='OK',start_ms=END-3600000,end_ms=END,buy_share=share,price_return_pct=change)


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        env=patch.dict(os.environ,{'LS_STATE_DIR':self.temp.name});env.start();self.addCleanup(env.stop)
        self.c=store.connect();self.addCleanup(self.c.close)
        self.k=initialize(str(Path(self.temp.name)/'binance_futures_kline_shadow.db'));self.addCleanup(self.k.close)
        for end,price in [(END-3600000,100.),(END,103.)]:
            self.k.execute('''INSERT INTO futures_closed_klines(symbol,interval,open_ms,close_ms,
                open,high,low,close,volume,source,received_ms,quote_volume,trade_count,taker_buy_quote)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(SYMBOL,'5m',end-300000,end-1,price,price,price,price,100.,native.SOURCE,NOW,10000.,10,6000.))
        self.k.commit();self.write(payload())

    def write(self,p,received=NOW):
        c=native.database();c.execute('INSERT OR REPLACE INTO snapshots VALUES(?,?,?)',(SYMBOL,received,json.dumps(p)));c.commit();c.close()

    def capture(self,side='LONG',f=None):
        with patch.object(native,'relay_get',side_effect=AssertionError('network not allowed')):
            return context.capture(self.k,SYMBOL,side,NOW,flow() if f is None else f)

    def test_valid_native_and_aligned_hour_are_frozen_without_network(self):
        r=self.capture();self.assertEqual(r['groups']['flow'],'SUPPORT')
        self.assertEqual(r['groups']['oi_price'],'WITH_SIGNAL_BUILD');self.assertEqual(r['groups']['funding_cost'],'COST')
        self.assertEqual(r['groups']['availability'],'COMPLETE');self.assertAlmostEqual(r['native']['oi_change_1h'],12.)
        self.assertEqual(r['oi_price_window']['start_ms'],END-3600000)

    def test_oi_increase_is_not_a_long_vote_and_decline_not_a_veto(self):
        r=self.capture('SHORT',flow(.4,-3.))
        self.assertEqual(r['groups']['flow'],'SUPPORT')
        self.assertEqual(r['groups']['oi_price'],'AGAINST_SIGNAL_BUILD')
        self.assertEqual(r['groups']['funding_cost'],'RECEIPT')
        p=payload()
        for i,row in enumerate(p['oi_history']):row['sumOpenInterest']=str(112-i)
        self.write(p);r=self.capture()
        self.assertEqual(r['groups']['oi_price'],'WITH_SIGNAL_UNWIND');self.assertEqual(r['groups']['flow'],'SUPPORT')

    def test_flow_mixed_conflict_neutral_and_missing_are_distinct(self):
        for f,label in [(flow(.6,-2),'MIXED'),(flow(.4,-2),'CONFLICT'),(flow(.5,1),'NEUTRAL'),({'status':'INCOMPLETE'},'MISSING')]:
            self.assertEqual(self.capture(f=f)['groups']['flow'],label)

    def test_stale_future_and_wrong_venue_cannot_become_supporting_evidence(self):
        for kind in ('stale','future','spot'):
            p=payload()
            if kind=='stale':p['observed_ms']=NOW-180001
            if kind=='future':p['observed_ms']=NOW+1
            if kind=='spot':p['source']='BINANCE_SPOT'
            self.write(p);r=self.capture();self.assertEqual(r['groups']['oi_price'],'MISSING')
            self.assertEqual(r['groups']['funding_cost'],'MISSING');self.assertEqual(r['groups']['flow'],'SUPPORT')

    def test_future_field_rejected_even_inside_clock_tolerance(self):
        p=payload();p['oi']['time']=NOW+1000;self.write(p);r=self.capture()
        self.assertEqual(r['groups']['oi_price'],'MISSING');self.assertEqual(r['groups']['funding_cost'],'COST')

    def test_missing_or_late_price_does_not_substitute_another_hour(self):
        self.k.execute("UPDATE futures_closed_klines SET received_ms=?",(NOW+1,));self.k.commit()
        r=self.capture();self.assertEqual(r['groups']['oi_price'],'MISSING');self.assertTrue(r['native']['oi_fresh'])

    def test_missing_funding_is_not_zero_or_a_vote(self):
        p=payload();p['mark']={};self.write(p);r=self.capture()
        self.assertEqual(r['groups']['funding_cost'],'MISSING');self.assertEqual(r['groups']['oi_price'],'WITH_SIGNAL_BUILD')

    def test_append_only_evidence_survives_later_change(self):
        original=self.capture();context.record(self.c,'id','SIGNAL',original);self.c.commit()
        revised=self.capture(f=flow(.4,-3));context.record(self.c,'id','SIGNAL',revised);self.c.commit()
        saved=json.loads(self.c.execute('SELECT payload FROM context_snapshots WHERE id=?',('id',)).fetchone()[0])
        self.assertEqual(saved,original);self.assertEqual(saved['groups']['flow'],'SUPPORT')


class PathAndReportTests(unittest.TestCase):
    def test_direction_and_stop_loss_are_separate_at_every_horizon(self):
        paths=fixtures.Paths();p=paths.plan();g=store.initial_progress(p)
        rows=[paths.bar(60000,94.,101.,96.)]
        rows.extend(paths.bar(120000+i*60000,99.,121.,120.) for i in range(59))
        g,outs=store.advance_path(p,g,rows)
        self.assertEqual([r['horizon_hours'] for r in outs],[.25,.5,1])
        for r in outs:
            self.assertEqual(r['market_direction'],'UP');self.assertEqual(r['direction_verdict'],'ALIGNED')
            self.assertAlmostEqual(r['plan_exit']['net_return_pct'],-5.3)
            self.assertIn('20',r['post_stop_milestones'])
            self.assertEqual(r['horizon_price'],120.)

    def test_short_down_is_correct_and_later_bar_does_not_change_15m(self):
        paths=fixtures.Paths();p=paths.plan(side='SHORT');rows=[paths.bar(60000+i*60000,97.,101.,98.) for i in range(15)]
        rows.append(paths.bar(960000,99.,130.,125.))
        _,out=store.advance_path(p,store.initial_progress(p),rows)
        self.assertEqual(out[0]['market_direction'],'DOWN');self.assertEqual(out[0]['direction_verdict'],'ALIGNED')
        self.assertIsNone(out[0]['plan_exit']);self.assertEqual(out[0]['horizon_price'],98.)

    def test_report_keeps_missing_unmature_boundary_and_direction_separate(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'LS_STATE_DIR':tmp}):
            c=store.connect();now=NOW+7200000
            base=dict(symbol=SYMBOL,price=100.,direction='LONG',model='WAKE_UP',strategy_id='sid',
                market_context={'observer_id':'observer','groups':{'flow':'SUPPORT','oi_price':'WITH_SIGNAL_BUILD','funding_cost':'COST','availability':'COMPLETE'}})
            for eid,delta,state,ctx in [('a',0,'TRACKING',True),('b',0,'DATA_PENDING',True),('c',0,'TRACKING',True),
                                         ('d',7100000,'TRACKING',True),('e',0,'TRACKING',False)]:
                p=copy.deepcopy(base)
                if not ctx:p.pop('market_context')
                c.execute('INSERT INTO ideas VALUES(?,?,?,?,?,?,?,?)',(eid,SYMBOL,NOW+delta,NOW+delta,state,json.dumps(p),'{}',now))
            outcome=dict(directional_return_pct=20.,max_favorable_pct=21.,max_adverse_pct=6.,milestones={'10':[1,2]},
                post_stop_milestones={'10':[3,4]},reference_net_return_pct=-5.3,plan_exit={'kind':'STOP','net_return_pct':-5.3})
            c.execute('INSERT INTO outcomes VALUES(?,?,?,?)',('a',1,'RESOLVED',json.dumps(outcome)))
            c.execute('INSERT INTO outcomes VALUES(?,?,?,?)',('b',1,'DATA_PENDING','{}'))
            ambiguous=dict(outcome,plan_exit=None);ambiguous.pop('reference_net_return_pct')
            c.execute('INSERT INTO outcomes VALUES(?,?,?,?)',('c',1,'UNRESOLVED_BOUNDARY',json.dumps(ambiguous)));c.commit()
            r=reporting.report(c,now,NOW,None,[1]);groups=r['horizons'][0]['groups']
            supported=next(x for x in groups if x['axis']=='flow' and x['group']=='SUPPORT')
            self.assertEqual(supported['counts']['signals'],4);self.assertEqual(supported['counts']['not_mature'],1)
            self.assertEqual(supported['counts']['data_gap'],1);self.assertEqual(supported['counts']['boundary_ambiguous'],1)
            self.assertEqual(supported['direction_correct_pct'],100.)
            self.assertEqual(supported['closed_plan_positive_pct'],0.)
            self.assertAlmostEqual(supported['closed_plan_mean_net_pct'],-5.3)
            self.assertEqual(supported['counts']['plan_closed'],1)
            self.assertTrue(any(x['group']=='LEGACY_NO_CONTEXT' for x in groups))
            self.assertEqual(len(list(reporting.details(c,NOW,now+1))),5);c.close()

    def test_empty_report_and_zero_net_are_not_fake_success(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'LS_STATE_DIR':tmp}):
            c=store.connect();r=reporting.report(c,NOW);self.assertEqual(len(r['horizons']),7)
            self.assertTrue(all(not h['groups'] for h in r['horizons']));c.close()
            self.assertIsNone(reporting.Stats().result()['direction_correct_pct'])


if __name__=='__main__':unittest.main()
