"""Synthetic fixtures only for correctness; never used in historical returns."""
import ast
from dataclasses import replace
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from hourly_motor_engine import *
from hourly_motor_backtest import simulate_trade,check_funding,assess
from trend_breakout_backtest import resolve_hour


def bars(n,interval=HOUR,start=0,price=100):
    return [Candle(start+i*interval,start+(i+1)*interval,price,price+1,price-1,price,100,10000) for i in range(n)]


def daily_fixture():
    d=[Candle(i*DAY,(i+1)*DAY,100+i,103+i,97+i,100+i,100,10000) for i in range(90)]
    return d,bars(90,DAY)


def signal_fixture(side='LONG'):
    h=bars(80,start=90*DAY-80*HOUR)
    h[-1]=replace(h[-1],high=104,close=103,volume=150)
    if side=='SHORT':h=[replace(b,open=200-b.open,high=200-b.low,low=200-b.high,close=200-b.close) for b in h]
    direction=dict(symbol='TESTUSDT',daily_at=90*DAY,direction=side,status='VALID')
    return evaluate('TESTUSDT',h,direction,90*DAY),h,direction


class EngineTests(unittest.TestCase):
    def test_daily_direction(self):
        d,b=daily_fixture();x=daily_direction('TESTUSDT',d,b,90*DAY+4*HOUR)
        self.assertEqual(x['direction'],'LONG');self.assertGreater(x['return14'],x['btc_return14'])

    def test_btc_not_stronger_than_itself(self):
        d,_=daily_fixture();self.assertIsNone(daily_direction('BTCUSDT',d,d,90*DAY)['direction'])

    def test_daily_lookahead_and_unclosed(self):
        d,b=daily_fixture();at=90*DAY
        x=daily_direction('TESTUSDT',d,b,at)
        self.assertEqual(x,daily_direction('TESTUSDT',d+bars(5,DAY,at,1000),b+bars(5,DAY,at,10),at))

    def test_hourly_lookahead(self):
        s,h,d=signal_fixture();at=s['time']
        self.assertEqual(s,evaluate('TESTUSDT',h+bars(5,HOUR,at,200),d,at))
        self.assertEqual(s['status'],'SIGNAL')

    def test_signal_excluded_from_level_volume(self):
        s,h,d=signal_fixture();self.assertEqual(s['level'],101);self.assertEqual(s['volume_ratio'],1.5)
        h[-1]=replace(h[-1],volume=149.999)
        self.assertEqual(evaluate('TESTUSDT',h,d,s['time'])['status'],'NO_SIGNAL')

    def test_strict_breakout(self):
        s,h,d=signal_fixture();h[-1]=replace(h[-1],close=101)
        self.assertEqual(evaluate('TESTUSDT',h,d,s['time'])['status'],'NO_SIGNAL')

    def test_long_short_symmetry(self):
        a,_,_=signal_fixture();b,_,_=signal_fixture('SHORT')
        pa=make_plan(a,103,a['time']);pb=make_plan(b,97,b['time'])
        self.assertAlmostEqual(pa['risk'],pb['risk']);self.assertAlmostEqual(pa['target'],200-pb['target'])
        self.assertAlmostEqual(pa['stop'],200-pb['stop'])

    def test_control_definitions(self):
        s,h,d=signal_fixture();d['direction']='SHORT'
        self.assertEqual(evaluate('TESTUSDT',h,d,s['time'],'CONTROL_A')['status'],'SIGNAL')
        self.assertEqual(evaluate('TESTUSDT',h,d,s['time'],'CONTROL_B')['status'],'NO_SIGNAL')
        d['direction']=None
        self.assertEqual(evaluate('TESTUSDT',h,d,s['time'],'CONTROL_B')['status'],'SIGNAL')
        d['status']='UNVERIFIED'
        self.assertEqual(evaluate('TESTUSDT',h,d,s['time'],'CONTROL_B')['status'],'NO_SIGNAL')

    def test_universe_past_only_stables_excluded(self):
        at=60*DAY;data={f'C{i:02d}USDT':bars(60,DAY,price=100+i) for i in range(35)}
        for i,(s,bb) in enumerate(data.items()):data[s]=[replace(b,quote_volume=100+i) for b in bb]
        data['USDCUSDT']=[replace(b,quote_volume=1e12) for b in bars(60,DAY)]
        x=universe_at(data,at);future={s:bb+bars(5,DAY,at,1000) for s,bb in data.items()}
        self.assertEqual(x,universe_at(future,at));self.assertEqual(len(x),30)
        self.assertNotIn('USDCUSDT',{r['symbol'] for r in x})
        self.assertTrue(all(r['last_used_ms']<=at for r in x))

    def test_gap_resets_indicator(self):
        d,b=daily_fixture();del d[70]
        self.assertEqual(daily_direction('TESTUSDT',d,b,90*DAY)['status'],'UNVERIFIED')

    def test_universe_has_no_unrequested_minimum_age(self):
        recent=[replace(b,quote_volume=100000) for b in bars(10,DAY,20*DAY)]
        old=bars(30,DAY)
        rows=universe_at({'NEWUSDT':recent,'OLDUSDT':old},30*DAY)
        self.assertEqual(rows[0]['symbol'],'NEWUSDT');self.assertEqual(rows[0]['observed_days'],10)

    def test_daily_extension_boundary(self):
        d,b=daily_fixture();d[-1]=replace(d[-1],high=240,close=240)
        self.assertEqual(daily_direction('TESTUSDT',d,b,90*DAY)['reason'],'EXTENDED')

    def test_stop_target_and_two_hour_timeout(self):
        s,_,_=signal_fixture();p=make_plan(s,103,s['time']);at=p['entry_ms']
        self.assertAlmostEqual(p['target']-p['entry'],1.5*p['risk'])
        events=[dict(time=at-8*HOUR,rate=.0001),dict(time=at,rate=.0001),dict(time=at+8*HOUR,rate=.0001)]
        h=bars(3,HOUR,at,103);r=simulate_trade(p,h,lambda *a:[],events,at+3*HOUR)
        self.assertEqual(r['outcome'],'TIMEOUT');self.assertEqual(r['exit_ms'],at+2*HOUR)
        self.assertAlmostEqual(r['net_pct'],-.30)

    def test_gap_stop(self):
        p=dict(symbol='T',side='LONG',stop=95,target=110)
        b=Candle(0,HOUR,90,100,89,99,10,100)
        r=resolve_hour(p,b,lambda *x:[]);self.assertEqual(r['exit'],90);self.assertEqual(r['resolution'],'OPEN_GAP')

    def test_ambiguous_hour_1m_and_fallback(self):
        p=dict(symbol='T',side='LONG',stop=95,target=110)
        h=Candle(0,HOUR,100,111,94,100,100,10000)
        m=bars(60,MINUTE);m[1]=replace(m[1],high=111);m[2]=replace(m[2],low=94)
        r=resolve_hour(p,h,lambda *a:m);self.assertEqual(r['outcome'],'HEDEF');self.assertEqual(r['resolution'],'1M_RESOLVED')
        r=resolve_hour(p,h,lambda *a:[]);self.assertEqual(r['outcome'],'STOP');self.assertEqual(r['resolution'],'1M_MISSING_OR_INVALID_STOP')
        m[1]=replace(m[1],low=94)
        self.assertEqual(resolve_hour(p,h,lambda *a:m)['outcome'],'STOP')

    def test_funding_sign_and_entry_boundary(self):
        events=[dict(time=0,rate=.0001),dict(time=HOUR,rate=.0002)]
        self.assertAlmostEqual(funding_percent('LONG',events,0,HOUR),.02)
        self.assertAlmostEqual(funding_percent('SHORT',events,0,HOUR),-.02)
        with self.assertRaises(ValueError):check_funding(dict(entry_ms=0,side='LONG'),2*HOUR,events)

    def test_pass_requires_all_controls_and_stress(self):
        a=dict(n=300,mean_net_pct=.1,stress_mean_pct=-.2);b=dict(n=500,mean_net_pct=-.1)
        self.assertEqual(assess(a,b,b)[0],'BAŞARISIZ');a['stress_mean_pct']=.01
        self.assertEqual(assess(a,b,b)[0],'GEÇTİ')

    def test_historical_live_share_functions(self):
        import hourly_motor_engine as engine
        import hourly_motor_backtest as historical
        import hourly_motor_live as live
        for name in ('daily_direction','evaluate','make_plan','result'):
            self.assertIs(getattr(historical,name),getattr(engine,name))
            self.assertIs(getattr(live,name),getattr(engine,name))

    def test_live_refuses_unapproved_report(self):
        from hourly_motor_live import approve
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'approval').write_text('{"approved_by_user":false}')
            (p/'summary').write_text('{"status":"GEÇTİ"}');(p/'report').write_text('test')
            with self.assertRaises(PermissionError):approve(p/'approval',p/'summary',p/'report')

    def test_no_scheduled_live_workflow(self):
        text=Path('.github/workflows/hourly-motor-live.yml').read_text()
        self.assertNotIn('cron:',text);self.assertIn("HOURLY_MOTOR_LIVE_ENABLED == 'true'",text)

    def test_live_partial_entry_minute_does_not_use_pre_entry_low(self):
        from hourly_motor_live import paper_exit
        class FakeMarket:
            def ticks(self,s,start,end):return [(start,100)]
            def candles(self,s,interval,start,end):return bars((end-start)//MINUTE,MINUTE,start,100)
        p=dict(symbol='T',side='LONG',entry_ms=30_000,deadline_ms=30_000+2*HOUR,stop=95,target=110)
        event,at=paper_exit(p,FakeMarket(),2*MINUTE)
        self.assertIsNone(event);self.assertEqual(at,2*MINUTE)

    def test_replay_one_accepted_plan_per_utc_day(self):
        import gzip
        from dataclasses import asdict,astuple
        from hourly_motor_backtest import candidates
        from trend_breakout_data import month
        d,btc=daily_fixture();_,h,_=signal_fixture();at=90*DAY
        h.extend(Candle(at+i*HOUR,at+(i+1)*HOUR,104+i,105+i,102+i,104.8+i,300,30000) for i in range(5))
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'prepared').mkdir()
            for s,dd in [('TESTUSDT',d),('BTCUSDT',btc)]:
                with gzip.open(root/'prepared'/(s+'.json.gz'),'wt') as f:json.dump(dict(daily=[asdict(b) for b in dd],hourly=[astuple(b) for b in h]),f)
            (root/'universe.json').write_text(json.dumps(dict(start=at,end=at+4*HOUR,months={month(at):[dict(symbol='TESTUSDT')]})))
            (root/'manifest.json').write_text('{}')
            rows=candidates(root,root/'out')
            self.assertEqual(sum(r['status']=='PLAN' for r in rows),1)
            self.assertGreater(sum(r['reason']=='DAILY_QUOTA' for r in rows),0)


if __name__=='__main__':unittest.main()
