import hashlib
import json
import math
from pathlib import Path
import statistics
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch
import abnormal_day_engine as engine
import abnormal_day_backtest as backtest
import abnormal_day_live as live
from abnormal_day_data import universe_at
from trend_breakout_live import State
from trend_breakout_data import ms
from abnormal_day_engine import Candle,DAY,HOUR


START=ms('2021-01-01')


def bars(n=130,shock=.20,start=START):
    result=[];price=100.
    for i in range(n):
        ret=(.01 if i%2 else -.009) if i<n-1 else shock
        close=price*(1+ret)
        result.append(Candle(start+i*DAY,start+(i+1)*DAY,price,max(price,close)*1.003,min(price,close)*.997,close,100.,10000.))
        price=close
    return result


class RulesTests(unittest.TestCase):
    def test_exact_prior_90_sample_statistics(self):
        b=bars();s=engine.evaluate('BTCUSDT',b,b[-1].close_ms)
        returns=[b[i].close/b[i-1].close-1 for i in range(len(b)-91,len(b)-1)]
        self.assertEqual(len(returns),90);self.assertAlmostEqual(s['m'],statistics.mean(returns));self.assertAlmostEqual(s['s'],statistics.stdev(returns))
        self.assertAlmostEqual(s['z'],(.2-s['m'])/s['s']);self.assertEqual(s['side'],'LONG')

    def test_signal_excludes_future_and_open_candles(self):
        b=bars();at=b[-1].close_ms;expected=engine.evaluate('BTCUSDT',b,at)
        future=bars(30,start=at);self.assertEqual(expected,engine.evaluate('BTCUSDT',b+future,at))
        self.assertNotEqual(engine.evaluate('BTCUSDT',b,at-1)['status'],'SIGNAL')

    def test_negative_shock_short(self):
        b=bars(shock=-.20);self.assertEqual(engine.evaluate('BTCUSDT',b,b[-1].close_ms)['side'],'SHORT')

    def test_wilder_atr14(self):
        b=bars();series=engine.DailySeries(b)
        tr=[max(x.high-x.low,abs(x.high-b[i-1].close),abs(x.low-b[i-1].close)) if i else x.high-x.low for i,x in enumerate(b)]
        expected=sum(tr[:14])/14
        for v in tr[14:]:expected=(expected*13+v)/14
        self.assertAlmostEqual(series.atr[-1],expected)

    def test_missing_and_zero_deviation(self):
        b=bars();self.assertEqual(engine.evaluate('AUSDT',b[:120]+b[121:],b[-1].close_ms)['reason'],'DATA_GAP')
        flat=[Candle(START+i*DAY,START+(i+1)*DAY,100,101,99,100,1,100) for i in range(130)]
        self.assertEqual(engine.evaluate('AUSDT',flat,flat[-1].close_ms)['reason'],'ZERO_STANDARD_DEVIATION')

    def plan(self,side='LONG'):
        return engine.make_plan(dict(symbol='BTCUSDT',side=side,atr=5.,z=2.5,time=START,status='SIGNAL'),100.,START)

    def test_gap_and_stop_long_short(self):
        p=self.plan();b=Candle(START,START+HOUR,88,101,87,98,1,100)
        self.assertEqual(engine.emergency_exit(p,b)['exit'],88)
        self.assertEqual(engine.emergency_exit(p,replace(b,open=100))['exit'],90)
        q=self.plan('SHORT');self.assertEqual(engine.emergency_exit(q,replace(b,open=115,high=116,low=100))['exit'],115)
        self.assertEqual(engine.emergency_exit(q,replace(b,open=100,high=115))['exit'],110)

    def test_deadline_is_exact_24_hours(self):
        p=self.plan();self.assertIsNone(engine.timed_exit(p,101,START+DAY-1))
        self.assertEqual(engine.timed_exit(p,101,START+DAY)['exit_ms'],START+DAY)
        self.assertEqual(engine.make_plan(p,100,START,48)['deadline_ms'],START+2*DAY)

    def test_actual_funding_sign_and_boundary(self):
        events=[dict(time=START,rate=.0001),dict(time=START+8*HOUR,rate=.0002),dict(time=START+DAY,rate=-.0001)]
        self.assertAlmostEqual(engine.funding_pct('LONG',events,START,START+DAY),.01)
        self.assertAlmostEqual(engine.funding_pct('SHORT',events,START,START+DAY),-.01)

    def test_cost_and_r(self):
        r=engine.result(self.plan(),102,.01)
        self.assertAlmostEqual(r['net_return_pct'],1.69);self.assertAlmostEqual(r['net_r'],.169)
        self.assertAlmostEqual(r['stress_net_return_pct'],1.39)

    def test_limits_rank_by_absolute_z(self):
        p=[dict(symbol=f'C{i}USDT',side='LONG' if i<5 else 'SHORT',z=(10-i) if i<5 else -(10-i)) for i in range(8)]
        taken,skipped=engine.choose(list(reversed(p)),[],True)
        self.assertEqual(len(taken),5);self.assertEqual(sum(x['side']=='LONG' for x in taken),3)
        self.assertEqual(taken[0]['symbol'],'C0USDT');self.assertEqual(len(skipped),3)
        a,b=engine.choose(p,[dict(symbol='C0USDT',side='LONG')],False)
        self.assertEqual(len(a),7);self.assertEqual(b[0]['reason'],'SAME_SYMBOL_OPEN')

    def test_controls_fixed_and_order_independent(self):
        sig=dict(symbol='BTCUSDT',time=START+15*DAY,signal_day_ms=START+14*DAY,side='SHORT')
        normal=[dict(symbol='BTCUSDT',time=START+(i+1)*DAY,signal_day_ms=START+i*DAY,z=.2,atr=1.) for i in range(28)]
        a=engine.select_control(sig,normal);b=engine.select_control(sig,list(reversed(normal)))
        self.assertEqual(a,b);self.assertEqual(a['side'],'SHORT');self.assertLess(abs(a['z']),1)
        self.assertIsNone(engine.select_control(sig,[]))

    def test_universe_only_past_top20_age120(self):
        b=bars(130,shock=0,start=ms('2020-01-01'));at=b[-1].close_ms
        data={f'C{i}USDT':[replace(x,quote_volume=100+i) for x in b] for i in range(25)}
        data['USDCUSDT']=[replace(x,quote_volume=1e12) for x in b];data['NEWUSDT']=b[-119:]
        expected=universe_at(data,at);self.assertEqual(len(expected),20);self.assertEqual(expected[0]['symbol'],'C24USDT')
        future=bars(5,start=at)
        self.assertEqual(expected,universe_at({s:v+future for s,v in data.items()},at))
        self.assertNotIn('USDCUSDT',{r['symbol'] for r in expected});self.assertNotIn('NEWUSDT',{r['symbol'] for r in expected})

    def test_shared_functions(self):
        for name in ('evaluate','make_plan','emergency_exit','timed_exit','funding_pct','result','choose'):
            self.assertIs(getattr(backtest,name),getattr(engine,name));self.assertIs(getattr(live,name),getattr(engine,name))

    def test_hourly_outcome_emergency_and_timeout(self):
        data=backtest.Data.__new__(backtest.Data);data.manifest={'end':START+2*DAY};data.events={'BTCUSDT':[]}
        data.funding=lambda p,t:0.
        mapping={START+i*HOUR:Candle(START+i*HOUR,START+(i+1)*HOUR,100,101,99,100,1,100) for i in range(49)}
        data.hour=lambda s,t:mapping.get(t)
        p=self.plan();r=data.outcome(p);self.assertEqual(r['exit_reason'],'TIME_24H');self.assertEqual(r['exit_ms'],START+DAY)
        mapping[START+4*HOUR]=replace(mapping[START+4*HOUR],low=85)
        self.assertEqual(data.outcome(p)['exit'],90);self.assertEqual(data.outcome(p,False)['exit_reason'],'TIME_24H')
        del mapping[START+3*HOUR];self.assertEqual(data.outcome(p)['status'],'UNVERIFIED')

    def test_verdict_requirements(self):
        s=dict(trades=200,mean_net_return_pct=.1,stress_mean_pct=.01,without_best_three_mean_pct=.02)
        c=dict(mean_net_return_pct=.05);years=[dict(total_net_return_pct=1),dict(total_net_return_pct=2),dict(total_net_return_pct=-1)]
        self.assertEqual(backtest.verdict(s,c,years)[0],'GEÇTİ')
        self.assertEqual(backtest.verdict(dict(s,trades=199),c,years)[0],'YETERSİZ VERİ')
        self.assertEqual(backtest.verdict(s,dict(mean_net_return_pct=.1),years)[0],'BAŞARISIZ')
        self.assertEqual(backtest.verdict(s,c,years,False)[0],'BAŞARISIZ')

    def test_portfolio_expires_before_next_same_coin_entry(self):
        data=type('D',(),dict(manifest={'start':START,'end':START+2*DAY}))()
        signals=[dict(symbol='BTCUSDT',side='LONG',atr=5.,z=3.,time=t,status='SIGNAL') for t in (START,START+DAY)]
        outcomes={}
        for s in signals:
            p=engine.make_plan(s,100,s['time']);outcomes[f"BTCUSDT:{s['time']}"]={'main':dict(p,**engine.timed_exit(p,102,p['deadline_ms']),**engine.result(p,102,0),status='CLOSED')}
        book=backtest.simulate(data,signals,outcomes,True)
        self.assertEqual(len(book['trades']),2);self.assertEqual(len(book['skips']),0)
        expected=10000*(1+.01*.17)**2
        self.assertAlmostEqual(book['curve'][-1]['equity'],expected)

    def test_live_approval_closed_and_no_schedule(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'r').write_text('report');(p/'s').write_text(json.dumps(dict(verdict='BAŞARISIZ')))
            (p/'a').write_text(json.dumps(dict(approved_by_user=True,rules_hash=engine.RULES_HASH,report_sha256=hashlib.sha256(b'report').hexdigest())))
            with self.assertRaises(PermissionError):live.approve(p/'a',p/'r',p/'s')
        source=Path('abnormal_day_live.py').read_text();self.assertNotIn('/fapi/v1/order',source)
        self.assertNotIn('schedule:',Path('.github/workflows/abnormal-day-live.yml').read_text())

    def test_outbox_durable_before_message_and_no_duplicate(self):
        with tempfile.TemporaryDirectory() as d:
            state=State(Path(d)/'p.sqlite');events=[]
            class Telegram:
                def send(self,text):events.append('send');return START,123
            save=lambda s:events.append('save')
            live.notify(state,'one','text',Telegram(),save);live.notify(state,'one','text',Telegram(),save)
            self.assertEqual(events,['save','send','save'])


if __name__=='__main__':unittest.main()
