"""Synthetic fixtures are engineering tests ONLY, never reported as market results."""
from dataclasses import replace
import inspect
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from trend_breakout_engine import Candle, Series, HOUR, DAY, MINUTE, evaluate, make_plan, choose_plans, _ema, _atr
from trend_breakout_data import select_universe, ms
from trend_breakout_backtest import resolve_hour, simulate_trade, funding_cost, portfolio, metrics, verdict


def candle(i, o=100., h=101., l=99., c=100., v=10., interval=HOUR):
    return Candle(i*interval,(i+1)*interval,o,h,l,c,v,v*c)


def fixture():
    h=[]
    for i in range(600):
        p=100+i*.02; h.append(candle(i,p,p+.5,p-.5,p))
    h[-1]=candle(599,111.98,113.4,111.6,113.2,100)
    q=[candle(i,100+i*.1,101+i*.1,99+i*.1,100+i*.1,interval=4*HOUR) for i in range(150)]
    return h,q,600*HOUR


def base_plan():
    return dict(symbol='TESTUSDT',side='LONG',entry=100.,entry_ms=0,stop=99.,target=102.,risk=1.,risk_pct=1.,quote_volume_24h=100.)


class EngineTests(unittest.TestCase):
    def test_exact_signal_and_plan(self):
        h,q,t=fixture(); r=evaluate('TESTUSDT',h,q,t)
        self.assertEqual(r['status'],'SIGNAL');self.assertEqual(r['side'],'LONG')
        self.assertAlmostEqual(r['level'],max(b.high for b in h[-121:-1]))
        self.assertAlmostEqual(r['volume_ratio'],10.)
        p=make_plan(r,113.2,t);self.assertEqual(p['status'],'PLAN')
        self.assertAlmostEqual(p['target']-p['entry'],2*(p['entry']-p['stop']))

    def test_future_invariance_raw_and_precomputed(self):
        h,q,t=fixture(); baseline=evaluate('TESTUSDT',h,q,t)
        futureh=h+[candle(600+i,200,201,199,200,100000) for i in range(120)]
        futureq=q+[candle(150+i,200,201,199,200,interval=4*HOUR) for i in range(30)]
        self.assertEqual(baseline,evaluate('TESTUSDT',futureh,futureq,t))
        self.assertEqual(baseline,evaluate('TESTUSDT',Series(futureh,HOUR),Series(futureq,4*HOUR),t))

    def test_current_hour_and_forming_four_hour_not_seen(self):
        h,q,t=fixture()
        h.append(candle(600,100,1000,1,999,100000))
        q.append(candle(150,100,1000,1,999,100000,4*HOUR))
        self.assertEqual(evaluate('TESTUSDT',h,q,t),evaluate('TESTUSDT',h[:-1],q[:-1],t))

    def test_current_bar_excluded_from_level_and_volume(self):
        h,q,t=fixture();r=evaluate('TESTUSDT',h,q,t)
        self.assertLess(r['level'],h[-1].high)
        h[-1]=replace(h[-1],volume=15)
        self.assertEqual(evaluate('T',h,q,t)['reason'],'LOW_VOLUME')

    def test_480_obstacle_excludes_signal(self):
        h,q,t=fixture();r=evaluate('T',h,q,t)
        self.assertEqual(r['obstacle'],max(b.high for b in h[-481:-1]))

    def test_strict_breakout_equality(self):
        h,q,t=fixture();level=max(b.high for b in h[-121:-1]);h[-1]=replace(h[-1],close=level)
        self.assertEqual(evaluate('T',h,q,t)['reason'],'NO_BREAKOUT')

    def test_long_short_price_symmetry(self):
        h,q,t=fixture()
        def mirror(b): return replace(b,open=400-b.open,high=400-b.low,low=400-b.high,close=400-b.close)
        a=evaluate('T',h,q,t);b=evaluate('T',list(map(mirror,h)),list(map(mirror,q)),t)
        self.assertEqual(b['side'],'SHORT')
        for k in ('level','stop','obstacle'):self.assertAlmostEqual(a[k]+b[k],400.)
        self.assertAlmostEqual(a['atr'],b['atr'])
        pa=make_plan(a,113.2,t);pb=make_plan(b,286.8,t)
        self.assertAlmostEqual(pa['target']+pb['target'],400.)

    def test_risk_cap_equality_and_excess(self):
        h,q,t=fixture();s=evaluate('T',h,q,t);edge=s['stop']+2.5*s['atr']
        self.assertEqual(make_plan(s,edge+1e-8,t)['reason'],'LATE_BREAKOUT')
        self.assertNotEqual(make_plan(s,edge-1e-8,t)['reason'],'LATE_BREAKOUT')

    def test_directional_stop_not_abs_only(self):
        h,q,t=fixture();s=evaluate('T',h,q,t)
        self.assertEqual(make_plan(s,s['stop']-.1,t)['reason'],'ENTRY_BEYOND_STOP')

    def test_obstacle_before_target(self):
        h,q,t=fixture();s=evaluate('T',h,q,t);s['obstacle']=114.
        self.assertEqual(make_plan(s,113.2,t)['reason'],'OBSTACLE_BEFORE_TARGET')

    def test_data_gap_rejected(self):
        h,q,t=fixture();h.pop(-10)
        self.assertEqual(evaluate('T',h,q,t)['reason'],'DATA_GAP')

    def test_indicator_reference(self):
        self.assertEqual(_ema([10.]*60,50)[-1],10.)
        self.assertEqual(_atr([candle(i) for i in range(30)])[-1],2.)


class UniverseTests(unittest.TestCase):
    def test_only_prior_month_data_and_age(self):
        at=100*DAY
        old=[candle(i,interval=DAY) for i in range(100)]
        future=old+[candle(i,v=1e12,interval=DAY) for i in range(100,140)]
        self.assertEqual(select_universe({'OLDUSDT':old},at),select_universe({'OLDUSDT':future},at))
        self.assertEqual(select_universe({'YOUNGUSDT':old[50:]},at),[])
        self.assertEqual(select_universe({'SPARSEUSDT':old[:5]+old[-30:]},at),[])
        self.assertEqual(select_universe({'USDCUSDT':old,'QQQUSDT':old},at),[])

    def test_volume_rank_and_delisted_still_eligible_at_old_date(self):
        low=[candle(i,interval=DAY) for i in range(100)]
        high=[replace(b,quote_volume=b.quote_volume*2) for b in low]
        r=select_universe({'DELISTEDUSDT':high,'STILLUSDT':low},100*DAY,1)
        self.assertEqual(r[0]['symbol'],'DELISTEDUSDT')
        self.assertEqual(r[0]['latest_used_ms'],100*DAY)


class OutcomeTests(unittest.TestCase):
    def minute_path(self,stop_first=False,both=False):
        rows=[candle(i,100,100.1,99.9,100,interval=MINUTE) for i in range(60)]
        rows[10]=candle(10,100,103,98 if both else 99.9,100,interval=MINUTE)
        rows[20]=candle(20,100,100.1,98,100,interval=MINUTE)
        if stop_first:rows[5]=candle(5,100,100.1,98,100,interval=MINUTE)
        return rows

    def test_ambiguous_hour_target_first(self):
        r=resolve_hour(base_plan(),candle(0,100,103,98,100),lambda s,t:self.minute_path())
        self.assertEqual((r['outcome'],r['resolution']),('HEDEF','1M_RESOLVED'))

    def test_ambiguous_hour_stop_first(self):
        r=resolve_hour(base_plan(),candle(0,100,103,98,100),lambda s,t:self.minute_path(True))
        self.assertEqual(r['outcome'],'STOP')

    def test_ambiguous_minute_stop(self):
        r=resolve_hour(base_plan(),candle(0,100,103,98,100),lambda s,t:self.minute_path(both=True))
        self.assertEqual(r['resolution'],'1M_BOTH_STOP')

    def test_missing_partial_or_inconsistent_minute_stop(self):
        for rows in ([],self.minute_path()[:-1],[candle(i,interval=MINUTE) for i in range(60)]):
            r=resolve_hour(base_plan(),candle(0,100,103,98,100),lambda s,t:rows)
            self.assertEqual((r['outcome'],r['resolution']),('STOP','1M_MISSING_OR_INVALID_STOP'))

    def test_adverse_gap(self):
        r=resolve_hour(base_plan(),candle(0,97,98,96,97),lambda s,t:[])
        self.assertEqual(r['exit'],97.)

    def test_full_168_hour_timeout_and_24h_excursion(self):
        rows=[candle(i,100,100.8,99.8,100.1) for i in range(168)]
        r=simulate_trade(base_plan(),rows,lambda s,t:[])
        self.assertEqual(r['outcome'],'TIMEOUT')
        self.assertAlmostEqual(r['mfe_pct'],.8);self.assertAlmostEqual(r['mae_pct'],.2)
        self.assertTrue(r['direction_correct']);self.assertLess(r['net_r'],0.)

    def test_mfe_continues_after_early_exit(self):
        rows=[candle(i,100,100.5,99.5,100) for i in range(24)]
        rows[0]=candle(0,100,102,99.5,101)
        rows[23]=candle(23,100,110,99.5,101)
        r=simulate_trade(base_plan(),rows,lambda s,t:[])
        self.assertEqual(r['outcome'],'HEDEF');self.assertAlmostEqual(r['mfe_pct'],10.)

    def test_missing_hour_not_fabricated(self):
        rows=[candle(i,100,100.5,99.5,100) for i in range(168) if i != 3]
        r=simulate_trade(base_plan(),rows,lambda s,t:[])
        self.assertEqual(r['outcome'],'UNVERIFIED_DATA_GAP');self.assertIsNone(r['net_r'])

    def test_signed_real_and_fallback_funding(self):
        p=base_plan();p['entry_ms']=ms('2026-09-01')
        funding=[dict(time=p['entry_ms']+8*HOUR,rate=.001,interval_hours=8)]
        l=funding_cost(p,p['entry_ms']+9*HOUR,funding,['2026-09'],[])
        sh=funding_cost(dict(p,side='SHORT'),p['entry_ms']+9*HOUR,funding,['2026-09'],[])
        self.assertAlmostEqual(l['funding_pct'],.1);self.assertAlmostEqual(sh['funding_pct'],-.1)
        f=funding_cost(p,p['entry_ms']+16*HOUR,[],[],[]);self.assertAlmostEqual(f['funding_pct'],.02)


class PortfolioTests(unittest.TestCase):
    def test_volume_priority_total_and_direction_limits(self):
        ps=[dict(base_plan(),symbol=f'C{i}USDT',side='LONG' if i<5 else 'SHORT',quote_volume_24h=100-i) for i in range(8)]
        a,r=choose_plans(ps,[],True)
        self.assertEqual(len(a),5);self.assertEqual(sum(p['side']=='LONG' for p in a),3)
        self.assertEqual([p['symbol'] for p in a],['C0USDT','C1USDT','C2USDT','C5USDT','C6USDT'])
        self.assertTrue(all(x['reason']=='LİMİT NEDENİYLE ATLANDI' for x in r))

    def test_unlimited_still_enforces_single_symbol(self):
        a,r=choose_plans([base_plan()], [base_plan()],False)
        self.assertEqual(a,[]);self.assertEqual(r[0]['reason'],'SAME_SYMBOL_OPEN')

    def test_slot_release_at_next_entry(self):
        a=dict(base_plan(),exit_ms=HOUR-1)
        b=dict(base_plan(),entry_ms=HOUR,exit_ms=2*HOUR-1)
        accepted,skipped=portfolio([a,b],True)
        self.assertEqual(len(accepted),2);self.assertEqual(skipped,[])

    def test_success_thresholds(self):
        m=dict(trades=100,unverified=0,mean_net_r=.1,mean_net_r_cost_060=.01,mean_net_r_without_best_three=.02)
        self.assertEqual(verdict(m),'BAŞARILI');self.assertEqual(verdict(dict(m,mean_net_r_cost_060=0)),'BAŞARISIZ')
        self.assertEqual(verdict(dict(m,trades=99)),'YETERSİZ VERİ')
        self.assertEqual(verdict(m,False),'YETERSİZ VERİ')


class LiveTests(unittest.TestCase):
    def test_locked_test_cannot_rerun_or_change_on_resume(self):
        from trend_breakout_backtest import acquire_seal
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);identity={'rules':'unchanged','data':'unchanged'}
            acquire_seal(p,identity)
            with self.assertRaises(FileExistsError):acquire_seal(p,identity)
            acquire_seal(p,identity,resume=True)
            with self.assertRaises(RuntimeError):acquire_seal(p,dict(identity,rules='changed'),resume=True)
            (p/'summary.json').write_text('{}')
            with self.assertRaises(RuntimeError):acquire_seal(p,identity,resume=True)

    def test_same_evaluate_function_object(self):
        import trend_breakout_live as live
        import trend_breakout_backtest as historical
        self.assertIs(live.evaluate,evaluate);self.assertIs(historical.evaluate,evaluate)
        self.assertIn('evaluate(symbol,h,q,t)',inspect.getsource(live.cycle))
        self.assertIn('evaluate(s,h,q,t)',inspect.getsource(historical.run))

    def test_catchup_every_hour_dedup_and_failed_symbol_retry(self):
        import trend_breakout_live as live
        h,q,t=fixture()
        class Market:
            sources={};failed=False
            def candles(self,symbol,interval,start,end,source=None):
                if interval=='1m':return []
                if symbol=='C49USDT' and not self.failed:
                    self.failed=True;raise RuntimeError('TEST_DATA_ERROR')
                self.sources[symbol]='futures'
                return h if interval=='1h' else q
            def price(self,symbol,source):return 113.2,t+1000
            def tick(self,symbol,source):return '0.01'
            def funding(self,*args):return [],[]
        universe=lambda m:[dict(symbol=f'C{i}USDT') for i in range(50)]
        with tempfile.TemporaryDirectory() as d:
            state=live.State(Path(d)/'separate.sqlite');state.set_meta('last_hour',t-2*HOUR)
            market=Market()
            with patch.object(live,'evaluate',wraps=evaluate) as ev:
                live.cycle(state,market,universe,at=t+1000)
                self.assertEqual({c.args[3] for c in ev.call_args_list},{t-HOUR,t})
            self.assertEqual(state.meta('last_hour'),t)
            self.assertEqual(len(state.active()),3)
            self.assertEqual(state.db.execute('SELECT count(*) FROM pending').fetchone()[0],2)
            live.cycle(state,market,universe,at=t+1000)
            self.assertEqual(state.db.execute('SELECT count(*) FROM pending').fetchone()[0],0)
            count=state.db.execute('SELECT count(*) FROM signals').fetchone()[0]
            live.cycle(state,market,universe,at=t+1000)
            self.assertEqual(state.db.execute('SELECT count(*) FROM signals').fetchone()[0],count)
            self.assertEqual(len(state.active()),3)

    def test_report_approval_is_required_and_hash_bound(self):
        import trend_breakout_live as live
        from trend_breakout_engine import RULES_HASH
        import hashlib
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);report=p/'report.md';report.write_text('test report')
            a=p/'approval.json';a.write_text(json.dumps(dict(approved_by_user=False)))
            with self.assertRaises(PermissionError):live.approval(a,report)
            a.write_text(json.dumps(dict(approved_by_user=True,rules_hash=RULES_HASH,report_sha256='wrong')))
            with self.assertRaises(PermissionError):live.approval(a,report)
            a.write_text(json.dumps(dict(approved_by_user=True,rules_hash=RULES_HASH,report_sha256=hashlib.sha256(report.read_bytes()).hexdigest())))
            self.assertTrue(live.approval(a,report))

    def test_durable_checkpoint_before_send_and_unknown_not_retried(self):
        import trend_breakout_live as live
        h,q,t=fixture();p=make_plan(evaluate('T',h,q,t),113.2,t)
        p['price_ms']=t
        events=[]
        class Sender:
            def send(self,*args):events.append('send');raise RuntimeError('UNKNOWN')
        def save(s):events.append('save')
        with tempfile.TemporaryDirectory() as d:
            state=live.State(Path(d)/'paper.sqlite')
            live.deliver(state,'one',p,'fake.png',Sender(),save)
            self.assertEqual(events,['save','send','save'])
            live.deliver(state,'one',p,'fake.png',Sender(),save)
            self.assertEqual(events,['save','send','save'])
            self.assertEqual(state.db.execute('SELECT state FROM outbox').fetchone()[0],'UNKNOWN')

    def test_failed_persistence_cannot_send(self):
        import trend_breakout_live as live
        class Sender:
            def send(self,*args):raise AssertionError('must never send')
        def fail(s):raise RuntimeError('persistence failed')
        with tempfile.TemporaryDirectory() as d:
            state=live.State(Path(d)/'paper.sqlite')
            with self.assertRaises(RuntimeError):live.deliver(state,'one',base_plan(),'fake.png',Sender(),fail)

    def test_entry_minute_does_not_use_pre_entry_extreme(self):
        from trend_breakout_live import paper_result
        p=dict(base_plan(),entry_ms=1000)
        rows=[candle(i,100,100.5,99.5,100,interval=MINUTE) for i in range(2)]
        rows[0]=candle(0,100,103,99.5,100,interval=MINUTE)
        self.assertEqual(paper_result(p,rows,2*MINUTE)['outcome'],'UNVERIFIED_ENTRY_MINUTE')

    def test_no_order_endpoints_or_exchange_credentials(self):
        import trend_breakout_live as live
        code=inspect.getsource(live)
        for forbidden in ('/fapi/v1/order','/api/v3/order','BINANCE_API_KEY','BINANCE_API_SECRET'):
            self.assertNotIn(forbidden,code)


if __name__=='__main__':unittest.main()
