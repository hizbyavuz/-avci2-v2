"""Synthetic engineering fixtures only; never backtest market observations."""
from dataclasses import replace
from pathlib import Path
import inspect
import json
import tempfile
import unittest
from unittest.mock import patch
from trend_breakout_engine import Candle,DAY,MINUTE
from trend_breakout_data import month
import trend_daily_engine as e
import trend_daily_backtest as bt
import trend_daily_live as live
from trend_daily_data import universe_at


def candle(i,o=100.,h=101.,l=99.,c=100.,v=10.):
    return Candle(i*DAY,(i+1)*DAY,o,h,l,c,v,v*c)


def fixture():
    bars=[candle(i) for i in range(120)]
    bars[-1]=candle(119,100.,106.,99.,105.)
    return bars


def plan(side='LONG',entry=105.,n=2.):
    return e.make_plan(dict(status='SIGNAL',symbol='TESTUSDT',side=side,n=n,time=120*DAY,volume_30d=100.),entry,120*DAY)


class SignalTests(unittest.TestCase):
    def test_signal_and_atr_exact(self):
        b=fixture();s=e.evaluate('TESTUSDT',b,120*DAY)
        self.assertEqual(s['side'],'LONG');self.assertEqual(s['level'],101.)
        self.assertAlmostEqual(s['n'],(19*2+7)/20)
        p=e.make_plan(s,110.,120*DAY);self.assertEqual(p['risk'],2*s['n']);self.assertEqual(p['stop'],110.-2*s['n'])
        self.assertNotIn('target',p)

    def test_lookahead_signal_and_stop(self):
        b=fixture();future=b+[candle(120+i,200,400,1,300) for i in range(30)]
        self.assertEqual(e.evaluate('T',b,120*DAY),e.evaluate('T',future,120*DAY))
        self.assertEqual(e.update_stop(plan(),b,120*DAY),e.update_stop(plan(),future,120*DAY))

    def test_strict_close_and_current_day_excluded(self):
        b=fixture();b[-1]=replace(b[-1],close=101.)
        self.assertEqual(e.evaluate('T',b,120*DAY)['status'],'NO_SIGNAL')
        b[-1]=replace(b[-1],close=105.,high=200.)
        self.assertEqual(e.evaluate('T',b,120*DAY)['level'],101.)

    def test_long_short_mirror(self):
        b=fixture();other=[replace(x,open=300-x.open,high=300-x.low,low=300-x.high,close=300-x.close) for x in b]
        a=e.evaluate('T',b,120*DAY);c=e.evaluate('T',other,120*DAY)
        self.assertEqual(c['side'],'SHORT');self.assertEqual(a['n'],c['n']);self.assertEqual(a['level']+c['level'],300.)

    def test_stop_only_favorable_uses_ten_closed_days(self):
        b=fixture();p=plan();p['stop']=95.
        self.assertEqual(e.update_stop(p,b,120*DAY)['stop'],99.)
        p['stop']=103.;self.assertEqual(e.update_stop(p,b,120*DAY)['stop'],103.)
        q=plan('SHORT');q['stop']=110.;self.assertEqual(e.update_stop(q,b,120*DAY)['stop'],106.)
        q['stop']=98.;self.assertEqual(e.update_stop(q,b,120*DAY)['stop'],98.)
        b[-1]=replace(b[-1],low=1.,high=500.);p['stop']=95.;self.assertEqual(e.update_stop(p,b,120*DAY)['stop'],95.)

    def test_gap_and_normal_stop(self):
        p=plan();self.assertEqual(p['stop'],101.)
        r=e.stop_exit(p,candle(120,95.,110.,90.,100.));self.assertEqual(r['exit'],95.);self.assertEqual(r['exit_ms'],120*DAY)
        r=e.stop_exit(p,candle(120,105.,110.,100.,106.));self.assertEqual(r['exit'],101.);self.assertIsNone(r['exit_ms'])
        q=plan('SHORT');r=e.stop_exit(q,candle(120,115.,116.,114.,115.));self.assertEqual(r['exit'],115.)
        minute=Candle(120*DAY+MINUTE,120*DAY+2*MINUTE,98.,100.,97.,99.,1.,99.)
        self.assertEqual(e.stop_exit(p,minute)['exit'],p['stop'])

    def test_data_gap_not_smoothed_silently(self):
        b=fixture();b.pop(50);self.assertEqual(e.evaluate('T',b,120*DAY)['reason'],'DATA_GAP')


class AccountingTests(unittest.TestCase):
    def test_funding_sign_and_boundaries(self):
        fs=[dict(time=0,rate=.1),dict(time=1000,rate=.001),dict(time=2000,rate=-.0002)]
        self.assertAlmostEqual(e.funding_pct('LONG',fs,0,2000),.08)
        self.assertAlmostEqual(e.funding_pct('SHORT',fs,0,2000),-.08)
        self.assertAlmostEqual(e.funding_pct('LONG',fs,1000,2000),-.02)

    def test_net_r_cost_and_funding(self):
        p=plan(entry=100,n=2);r=e.result(p,108.,.1)
        self.assertAlmostEqual(r['net_r'],(8-.3-.1)/4);self.assertAlmostEqual(r['stress_net_r'],(8-.6-.1)/4)
        self.assertAlmostEqual(r['funding_effect_r'],-.025)

    def test_five_day_direction_includes_after_early_exit(self):
        p=plan(entry=100);bars=[candle(120+i,100,100+i,99,100) for i in range(5)]
        self.assertTrue(e.direction_five(p,bars));self.assertIsNone(e.direction_five(p,bars[:4]))

    def test_mfe_mae_initial_r_units(self):
        p=plan(entry=100,n=2);r=e.excursions(p,[candle(120,100,112,96,105)])
        self.assertEqual(r,dict(mfe_r=3.,mae_r=1.))

    def test_six_total_four_direction_volume_order(self):
        rows=[dict(symbol=str(i),side='LONG' if i<6 else 'SHORT',volume_30d=100-i) for i in range(10)]
        accepted,rejected=e.choose(rows,[],True)
        self.assertEqual([p['symbol'] for p in accepted],['0','1','2','3','6','7'])
        self.assertEqual(len(rejected),4);self.assertTrue(all(p['reason']=='LİMİT NEDENİYLE ATLANDI' for p in rejected))
        self.assertEqual(len(e.choose(rows,[],False)[0]),10)
        a,r=e.choose(rows,[rows[0]],False);self.assertEqual(len(a),9);self.assertEqual(r[0]['reason'],'SAME_SYMBOL_OPEN')

    def test_verdict_exact_four_conditions_and_data(self):
        stats=dict(mean_net_r=.1,stress_mean_r=.01,without_best_three_mean_r=.02)
        years=[dict(total_net_r=x) for x in [1.,1.,-1.]]
        self.assertEqual(bt.judgement(stats,years)[0],'GEÇTİ')
        for key in stats:self.assertEqual(bt.judgement(dict(stats,**{key:0.}),years)[0],'BAŞARISIZ')
        self.assertEqual(bt.judgement(stats,[dict(total_net_r=1.),dict(total_net_r=-1.)])[0],'BAŞARISIZ')
        self.assertEqual(bt.judgement(stats,years,False)[0],'BAŞARISIZ')


class UniverseTests(unittest.TestCase):
    def test_only_closed_past_100_days_and_delisted(self):
        history=[candle(i) for i in range(120)]
        original=universe_at({'OLDUSDT':history},120*DAY)
        future=history+[candle(i,v=1e20) for i in range(120,150)]
        self.assertEqual(original,universe_at({'OLDUSDT':future},120*DAY))
        self.assertEqual(universe_at({'YOUNGUSDT':history[-99:]},120*DAY),[])
        self.assertEqual(universe_at({'USDCUSDT':history,'QQQUSDT':history},120*DAY),[])
        self.assertEqual(original[0]['symbol'],'OLDUSDT')


class IntegrationTests(unittest.TestCase):
    def test_same_functions_backtest_live(self):
        for name in ['evaluate','make_plan','update_stop','stop_exit','choose','result']:
            self.assertIs(getattr(bt,name),getattr(live,name));self.assertIs(getattr(bt,name),getattr(e,name))
        self.assertIn('evaluate(',inspect.getsource(bt.replay));self.assertIn('evaluate(',inspect.getsource(live.cycle))

    def test_live_requires_pass_and_bound_approval(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'a').write_text(json.dumps(dict(approved_by_user=False)));(p/'s').write_text(json.dumps(dict(verdict='GEÇTİ')));(p/'r').write_text('report')
            with self.assertRaises(PermissionError):live.approve(p/'a',p/'r',p/'s')
            import hashlib
            (p/'a').write_text(json.dumps(dict(approved_by_user=True,rules_hash=e.RULES_HASH,report_sha256=hashlib.sha256(b'report').hexdigest())))
            live.approve(p/'a',p/'r',p/'s');(p/'s').write_text(json.dumps(dict(verdict='BAŞARISIZ')))
            with self.assertRaises(PermissionError):live.approve(p/'a',p/'r',p/'s')

    def test_no_order_api_and_no_scheduled_live(self):
        source=inspect.getsource(live)
        for banned in ['/fapi/v1/order','/api/v3/order','create_order','BINANCE_API_SECRET']:self.assertNotIn(banned,source)
        workflow=Path('.github/workflows/trend-daily-live.yml').read_text();self.assertNotIn('schedule:',workflow)

    def test_daily_replay_capital_and_same_day_exit(self):
        bars=fixture()+[candle(120,105,106,100,103),candle(121,103,104,102,103)]
        class FakeData:
            series={'TESTUSDT':e.DailySeries(bars)}
            universe={'months':{month(120*DAY):[dict(symbol='TESTUSDT')]}}
            def bar(self,s,at):return bars[at//DAY] if at//DAY<len(bars) else None
            def funding_cost(self,p,at):return 0.,0,[]
            def resolve(self,p,b):return dict(exit=p['stop'],exit_ms=b.close_ms-1,exit_reason='STOP',resolution='SYNTHETIC_TEST',mfe_r=0.,mae_r=1.)
        with tempfile.TemporaryDirectory() as d:
            book=bt.replay(bt.new_book('limited'),FakeData(),120*DAY,122*DAY,Path(d)/'checkpoint')
        self.assertEqual(len(book['trades']),1);trade=book['trades'][0]
        self.assertAlmostEqual(trade['initial_risk_usdt'],100.)
        self.assertAlmostEqual(book['cash'],10000+trade['net_r']*100)
        self.assertEqual(book['positions'],[])

    def test_notify_saves_before_send_and_deduplicates(self):
        events=[]
        class Telegram:
            def send(self,text,photo):events.append('send');return 1,2
        with tempfile.TemporaryDirectory() as d:
            state=live.State(Path(d)/'paper.sqlite')
            live.notify(state,'x','hello',Telegram(),lambda s:events.append('save'))
            live.notify(state,'x','hello',Telegram(),lambda s:events.append('save'))
            self.assertEqual(events,['save','send','save'])
            state.db.close()

    def test_new_trailing_stop_is_not_retroactive_and_exit_outside_universe(self):
        bars=[candle(i,105.,107.,104.,105.) for i in range(122)]
        bars[120]=candle(120,105.,107.,102.,105.);bars[121]=candle(121,101.,106.,100.,105.)
        p=plan(entry=104.,n=2.);p.update(entry_ms=100*DAY,notional=1040.,quantity=10.,initial_risk_usdt=40.,last_price=105.)
        class FakeData:
            series={'TESTUSDT':e.DailySeries(bars)}
            universe={'months':{}}
            def bar(self,s,at):return bars[at//DAY]
            def funding_cost(self,p,at):return 0.,0,[]
            def resolve(self,p,b):raise AssertionError('old stop was not touched')
        book=bt.new_book('limited');book['positions']=[p]
        with tempfile.TemporaryDirectory() as d:book=bt.replay(book,FakeData(),120*DAY,122*DAY,Path(d)/'checkpoint')
        self.assertEqual(book['stops'][0]['stop'],102.)
        self.assertEqual(book['trades'][0]['exit_ms'],121*DAY)
        self.assertEqual(book['trades'][0]['exit'],101.)

    def test_minute_resolution_excludes_post_exit_extrema(self):
        p=plan();p['entry_ms']=120*DAY
        start=120*DAY
        minutes=[Candle(start,start+MINUTE,105.,107.,104.,106.,1.,100.),
                 Candle(start+MINUTE,start+2*MINUTE,106.,200.,100.,102.,1.,100.),
                 Candle(start+2*MINUTE,start+3*MINUTE,102.,999.,1.,100.,1.,100.)]
        with tempfile.TemporaryDirectory() as d:
            obj=object.__new__(bt.Data);obj.root=Path(d);obj.minutes=lambda s,t:minutes
            b=candle(120,105.,999.,1.,100.)
            r=obj.resolve(p,b);again=obj.resolve(p,b)
            self.assertEqual(r,again);self.assertEqual(r['exit'],101.)
            self.assertEqual(r['exit_ms'],start+2*MINUTE-1)
            self.assertEqual(r['mfe_r'],.5);self.assertEqual(r['mae_r'],1.)

    def test_live_replays_all_missed_daily_closes(self):
        bars=[candle(i) for i in range(130)]
        class Market:
            sources={'TESTUSDT':'futures'}
            def candles(self,s,interval,start,end,source=None):return [b for b in bars if b.close_ms<=end]
        class Telegram:
            def send(self,text,photo):return 1,1
        with tempfile.TemporaryDirectory() as d:
            state=live.State(Path(d)/'paper.sqlite');state.set_meta('last_daily',120*DAY);state.set_meta('daily_seed',0)
            with patch.object(live,'universe_for',return_value=[dict(symbol='TESTUSDT')]),patch.object(live,'evaluate',wraps=e.evaluate) as evaluator:
                live.cycle(state,Market(),None,Telegram(),lambda s:s.checkpoint(),123*DAY+7*MINUTE)
                self.assertEqual([c.args[2] for c in evaluator.call_args_list],[121*DAY,122*DAY,123*DAY])
                live.cycle(state,Market(),None,Telegram(),lambda s:s.checkpoint(),123*DAY+8*MINUTE)
                self.assertEqual(evaluator.call_count,3)
            state.db.close()

    def test_missing_stop_history_is_explicit_not_crash_or_invented(self):
        bars=[candle(i,105.,107.,104.,105.) for i in range(122) if i!=119]
        p=plan(entry=104.,n=2.);p.update(entry_ms=100*DAY,notional=1040.,quantity=10.,initial_risk_usdt=40.,last_price=105.)
        class FakeData:
            series={'TESTUSDT':e.DailySeries(bars)}
            universe={'months':{}}
            def bar(self,s,at):return next((b for b in bars if b.open_ms==at),None)
            def funding_cost(self,p,at):return 0.,0,[]
        book=bt.new_book('limited');book['positions']=[p]
        with tempfile.TemporaryDirectory() as d:book=bt.replay(book,FakeData(),120*DAY,122*DAY,Path(d)/'checkpoint')
        self.assertEqual(book['positions'][0]['stop'],100.)
        self.assertTrue(book['errors']);self.assertEqual(book['stops'],[])


if __name__=='__main__':unittest.main()
