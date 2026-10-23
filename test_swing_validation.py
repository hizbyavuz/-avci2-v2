import unittest,copy
import numpy as np
from long_short_swing import HOUR,MINUTE,CONFIG_HASH,assess,geometry
from long_short_swing_outcomes import initial,advance
from swing_validation_engine import candle,screen,replay,control_symbol,control_plan,select_positions,day_stats,comparison

def minute(t,o,h,l,c):return dict(open_ms=t,close_ms=t+MINUTE-1,open=o,high=h,low=l,close=c)
def aggregate(rows):
    out={}
    for r in rows:
        t=r['open_ms']//HOUR*HOUR
        if t not in out:out[t]=dict(r,open_ms=t,close_ms=t+HOUR-1)
        else:out[t].update(high=max(out[t]['high'],r['high']),low=min(out[t]['low'],r['low']),close=r['close'])
    return out

class ValidationTests(unittest.TestCase):
    def test_frozen_config(self):self.assertEqual(CONFIG_HASH,'d899d56bf0dea0bc564c7907e4e79ad30b81e464de9021b6ecfd6d44e4577938')
    def fixture(self):
        a=np.array([[i*HOUR,100,101,99,100,100000,60000,1000] for i in range(240)],dtype=float)
        a[180,1:]=[100,101.6,99.5,101.3,1000000,700000,10000]
        return a
    def test_signal_prefix_invariant(self):
        a=self.fixture();t=181*HOUR
        x=screen('X',a[:181],0,t+1)[0];y=screen('X',a,0,t+1)[0]
        self.assertTrue(x);self.assertEqual(x,y)
        expected=assess('X',[candle(r) for r in a[12:181]],t+999)
        self.assertEqual(x[0]['stop'],expected['stop']);self.assertEqual(x[0]['trigger'],expected['trigger'])
    def test_entry_geometry_rechecked(self):
        p=screen('X',self.fixture(),0,240*HOUR)[0][0]
        self.assertEqual(geometry(p,p['stop']-.1),'INVALID_RISK')
        self.assertEqual(p['entry_ms'],p['signal_time']+MINUTE)
    def test_replay_exact_reference_and_trailing(self):
        # Full4h block after entry arms and raises stop, then next block exits.
        rows=[]
        for i in range(12*60):
            t=i*MINUTE
            o=100 if i<240 else 123 if i<480 else 115
            rows.append(minute(t,o,o+1,o-1,o))
        p=dict(direction='LONG',symbol='X',entry=100.,entry_ms=MINUTE,stop=90.,atr_1h=4.)
        state=advance(p,initial(p,MINUTE,100),rows[1:])
        got=replay(p,aggregate(rows),lambda s,t:[r for r in rows if t<=r['open_ms']<t+HOUR],12*HOUR)
        self.assertEqual(state['exit'],got['exit']);self.assertEqual(got['exit']['price'],115)
        self.assertTrue(all(b['stop']>=a['stop'] for a,b in zip(got['updates'],got['updates'][1:])))
        self.assertEqual(got['updates'][0]['effective_ms'],8*HOUR)
    def test_same_hour_stop_arm_order_minute(self):
        rows=[minute(i*MINUTE,100,101,99,100) for i in range(240)]
        rows[71]=minute(71*MINUTE,100,125,99,120)
        rows[75]=minute(75*MINUTE,100,101,89,90)
        p=dict(direction='LONG',symbol='X',entry=100.,entry_ms=MINUTE,stop=90.,atr_1h=4.)
        got=replay(p,aggregate(rows),lambda s,t:[r for r in rows if t<=r['open_ms']<t+HOUR],4*HOUR)
        self.assertEqual(got['exit']['at_ms'],75*MINUTE);self.assertEqual(got['exit']['price'],90)
    def test_missing_minute_stop_first(self):
        rows=[minute(i*MINUTE,100,101,99,100) for i in range(180)];rows[71]=minute(71*MINUTE,100,130,80,90)
        p=dict(direction='LONG',symbol='X',entry=100.,entry_ms=MINUTE,stop=90.,atr_1h=4.)
        got=replay(p,aggregate(rows),lambda s,t:[r for r in rows if t==0 and t<=r['open_ms']<t+HOUR],3*HOUR)
        self.assertEqual(got['exit']['price'],90);self.assertEqual(got['resolution']['MISSING_MINUTE_STOP_FIRST'],1)
    def test_gap_exits_at_worse_open(self):
        rows=[minute(i*MINUTE,100,101,99,100) for i in range(120)];rows[60]=minute(60*MINUTE,80,85,79,84)
        p=dict(direction='LONG',symbol='X',entry=100.,entry_ms=MINUTE,stop=90.,atr_1h=4.)
        got=replay(p,aggregate(rows),lambda s,t:[r for r in rows if t<=r['open_ms']<t+HOUR],2*HOUR)
        self.assertEqual(got['exit']['price'],80)
    def test_timeout_168h_plus_minute(self):
        rows=[minute(i*MINUTE,100,101,99,100) for i in range(168*60+2)]
        p=dict(direction='LONG',symbol='X',entry=100.,entry_ms=MINUTE,stop=90.,atr_1h=4.)
        got=replay(p,aggregate(rows),lambda s,t:[r for r in rows if t<=r['open_ms']<t+HOUR],169*HOUR)
        self.assertEqual(got['exit']['kind'],'TIMEOUT');self.assertEqual(got['exit']['at_ms'],168*HOUR+MINUTE-1)
    def test_seed_and_no_signal_control(self):
        a=control_symbol('A',100,['D','A','C','B'],{'C'})
        self.assertIn(a,['B','D']);self.assertEqual(a,control_symbol('A',100,['B','C','A','D'],{'C'}))
    def test_control_percentage_geometry(self):
        p=dict(entry=100.,stop=95.,atr_1h=2.)
        c=control_plan(p,'BTC',200.)
        self.assertEqual(c['stop'],190.);self.assertEqual(c['atr_1h'],4.)
    def test_limits_rank_known_volume_and_keep_skipped(self):
        pp=[dict(id=str(i),entry_ms=100,quote_volume_24h=i,symbol=str(i)) for i in range(8)]
        oo={p['id']:dict(exit=dict(time_upper_ms=200)) for p in pp}
        a=select_positions(pp,oo,5)
        self.assertEqual(a['selected'],['7','6','5','4','3']);self.assertEqual(a['skipped'],['2','1','0']);self.assertEqual(a['peak'],5)
        self.assertEqual(len(select_positions(pp,oo,None)['selected']),8)
    def test_daily_equal_weights(self):
        rows=[dict(signal_time=0,net_r=1.)]*100+[dict(signal_time=24*HOUR,net_r=-1.)]
        x=day_stats(rows);self.assertEqual(x['days'],2);self.assertEqual(x['mean'],0.);self.assertAlmostEqual(x['se'],1.)
    def test_comparison_day_basis(self):
        rr=[dict(id=str(i),signal_time=i*24*HOUR,net_r=2.,net_pct=3.) for i in range(20)]
        cc={r['id']:dict(net_r=1.,net_pct=2.) for r in rr}
        c=comparison(rr,cc);self.assertTrue(c['significant']);self.assertEqual(c['daily_r']['days'],20)
    def test_actual_funding_sign_and_mark_notional(self):
        from swing_validation_backtest import Data
        from swing_validation_data import START
        class Fake(Data):
            def __init__(self):pass
            def funding_month(self,s,m):return dict(events=[dict(time=START+i*8*HOUR,rate=rate,mark=200.,mark_source='TEST') for i,rate in [(0,.001),(1,.001),(2,-.0005),(3,.001)]])
        x=Fake().funding('X',START+HOUR,dict(kind='TIMEOUT',at_ms=START+17*HOUR,time_upper_ms=START+17*HOUR,entry_reference=100.))
        self.assertAlmostEqual(x['cost_pct'],.1)
        y=Fake().funding('X',START+HOUR,dict(kind='STOP',at_ms=START+16*HOUR,time_upper_ms=START+16*HOUR+MINUTE-1,entry_reference=100.))
        self.assertAlmostEqual(y['cost_pct'],.2) # ambiguous funding credit withheld

if __name__=='__main__':unittest.main()
