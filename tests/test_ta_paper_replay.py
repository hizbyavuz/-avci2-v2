import copy
from dataclasses import replace
import unittest
from ta_paper.core import CFG,MINUTE
from ta_paper.replay import execute,draw,window,controls,funding_evidence,portfolio,DAY,stats
from test_ta_paper import setup,candle,NOW


def event(direction='LONG'):
    p=setup(direction);p.update(id='test',arm='strategy',quote_volume=10_000_000,atr=2,trend=1 if direction=='LONG' else -1)
    return p


def rates_for(p):
    return [[p['entry_ms']-4*60*MINUTE,.0001,8],[p['entry_ms']+4*60*MINUTE,.0001,8]]


class ReplayTests(unittest.TestCase):
    def test_future_bar_never_changes_window(self):
        rows=[candle(i*MINUTE) for i in range(130)]
        now=125*MINUTE+3000
        a=window(rows,[r[0] for r in rows],now,MINUTE)
        rows[-1][4]=999999
        self.assertEqual(a,window(rows,[r[0] for r in rows],now,MINUTE))
        self.assertEqual(a[-1][0],124*MINUTE)

    def test_same_minute_stop_first_funding_bracketed_and_cancellation_not_loss(self):
        p=event();t=p['entry_ms']
        path={NOW:candle(NOW),t:candle(t,h=105,l=97)}
        result=execute(p,path,rates_for(p),{})
        self.assertEqual(result['status'],'CLOSED')
        self.assertEqual(result['reason'],'STOP')
        self.assertTrue(result['same_minute_ambiguous'])
        self.assertAlmostEqual(result['net_r'],-1)
        path[NOW]=candle(NOW,h=105,l=99)
        cancelled=execute(p,path,rates_for(p),{})
        self.assertEqual(cancelled['status'],'CANCELLED')
        self.assertNotIn('net_r',cancelled)

    def test_missing_funding_or_path_not_zero_filled(self):
        p=event();t=p['entry_ms'];path={NOW:candle(NOW),t:candle(t,h=105,l=99)}
        result=execute(p,path,[],{})
        self.assertEqual(result['status'],'FUNDING_UNRESOLVED')
        self.assertNotIn('net_r',result)
        del path[t]
        self.assertEqual(execute(p,path,[],{})['status'],'UNRESOLVED_DATA')
        with self.assertRaisesRegex(ValueError,'INTERVAL_GAP'):
            funding_evidence('TESTUSDT',[[0,.001,8],[24*60*MINUTE,.001,8]],{},MINUTE,2*MINUTE)

    def test_direction_control_is_fixed_and_risk_symmetric(self):
        p=event();p.update(status='SETUP')
        bars=[candle(i*MINUTE) for i in range(120)]
        a=controls(p,bars)[0];b=controls(copy.deepcopy(p),bars)[0]
        self.assertEqual(a,b)
        self.assertEqual(abs(a['target']-100),2*abs(a['stop']-100))
        self.assertEqual(draw('key'),draw('key'))

    def test_cost_stress_cannot_improve_same_target_trade(self):
        p=event();t=p['entry_ms'];path={NOW:candle(NOW),t:candle(t,h=105,l=99)}
        base=execute(p,path,rates_for(p),{})
        stress=execute(p,path,rates_for(p),{},replace(CFG,fee_each_side=.001,entry_slippage=.0004,target_slippage=.0004,stop_slippage=.002),2)
        self.assertEqual(stress['status'],'CLOSED')
        self.assertLess(stress['net_r'],base['net_r'])

    def test_portfolio_priority_unknown_risk_and_no_forced_horizon_exit(self):
        p=event();t=p['entry_ms']
        unknown=dict(p,status='OPEN_AT_HORIZON',initial_loss=5,planned_loss=5,marks=[])
        second=dict(unknown,id='b',symbol='OTHERUSDT',quote_volume=20_000_000)
        later=dict(unknown,id='c',symbol='THIRDUSDT',observed_ms=NOW+4*DAY,entry_ms=t+4*DAY)
        accepted,rejected,eq=portfolio([unknown,second,later])
        self.assertEqual([p['symbol'] for p in accepted],['OTHERUSDT'])
        self.assertEqual(rejected['SAME_DIRECTION_CLUSTER_LIMIT'],2)
        self.assertEqual(eq,1000)
        self.assertEqual(stats(accepted)['n_closed'],0)

if __name__=='__main__':unittest.main()
