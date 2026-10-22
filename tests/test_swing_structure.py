import copy
import os
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

import long_short_swing as core
import long_short_swing_outcomes as outcomes
import long_short_swing_runtime as runtime

H=core.HOUR;M=core.MINUTE
START=1_788_220_800_000


def bars(side='LONG'):
    rows=[]
    for i in range(169):
        t=START+i*H
        rows.append(dict(open_ms=t,close_ms=t+H-1,open=100.,high=101.,low=99.,close=100.,
                         quote_volume=100_000.,taker_buy_quote=50_000.))
    if side=='LONG':
        rows[-1].update(close=101.2,high=101.4,quote_volume=350_000.,taker_buy_quote=210_000.)
    else:
        rows[110]['high']=110.;rows[140]['high']=108.
        rows[-1].update(close=98.8,low=98.6,quote_volume=250_000.,taker_buy_quote=100_000.)
    return rows


def plan(side='LONG'):
    rows=bars(side)
    return core.assess('TESTUSDT',rows,rows[-1]['close_ms']+1000)


def minute(t,op=100.,hi=101.,lo=99.,cl=100.):
    return dict(open_ms=t,close_ms=t+M-1,open=op,high=hi,low=lo,close=cl)


class CoreTests(unittest.TestCase):
    def test_distinct_long_and_short_and_directional_origin(self):
        for side,model in [('LONG','BASE_BREAKOUT_LONG'),('SHORT','FAILED_BOUNCE_SHORT')]:
            p=plan(side)
            self.assertEqual(p['stage'],'READY',p)
            self.assertEqual(p['model'],model)
            self.assertGreater(p['move_before_signal_pct'],0)
        self.assertAlmostEqual(plan('SHORT')['move_before_signal_pct'],100*(1-98.8/110))

    def test_no_previous_ten_percent_rally_required(self):
        p=plan();self.assertLess(p['move_before_signal_pct'],3.);self.assertEqual(p['stage'],'READY')

    def test_short_needs_failed_bounce_not_just_mirrored_break(self):
        r=bars('SHORT');r[140]['high']=111.
        p=core.assess('TUSDT',r,r[-1]['close_ms']+1)
        self.assertEqual(p['reason'],'NO_CONFIRMED_LOWER_HIGH')

    def test_missing_future_gap_and_missing_taker_fail_closed(self):
        r=bars();now=r[-1]['close_ms']+1000
        self.assertEqual(core.assess('TUSDT',r[:-1],now)['reason'],'HISTORY_MISSING_169H')
        self.assertEqual(core.assess('TUSDT',r,now-H)['reason'],'UNCLOSED_BAR')
        bad=copy.deepcopy(r);bad[40]['open_ms']+=H
        self.assertEqual(core.assess('TUSDT',bad,now)['stage'],'REJECTED')
        bad=copy.deepcopy(r);bad[3]['taker_buy_quote']=None
        self.assertEqual(core.assess('TUSDT',bad,now)['reason'],'VOLUME_OR_TAKER_MISSING')
        self.assertEqual(core.assess('TUSDT',r,now+H)['reason'],'STALE_CONFIRMATION')

    def test_seven_same_hour_samples_and_strict_buy_share(self):
        r=bars();r[-1]['taker_buy_quote']=r[-1]['quote_volume']/2
        self.assertEqual(core.assess('TUSDT',r,r[-1]['close_ms']+1)['reason'],'LONG_VOLUME_OR_BUY_PRESSURE_WEAK')
        r=bars()
        for d in range(1,8):r[-1-24*d]['quote_volume']=200_000.
        self.assertEqual(core.assess('TUSDT',r,r[-1]['close_ms']+1)['reason'],'LONG_VOLUME_OR_BUY_PRESSURE_WEAK')

    def test_confirmed_pivots_never_use_future(self):
        r=bars('SHORT')[:141]
        self.assertNotIn(108.,[p['price'] for p in core.pivots(r,'high')])
        self.assertIn(108.,[p['price'] for p in core.pivots(bars('SHORT')[:143],'high')])

    def test_known_obstacle_requires_room_and_chase_fails(self):
        p=plan();p['obstacle']=102.
        self.assertEqual(core.geometry(p,p['price']),'INSUFFICIENT_KNOWN_ROOM')
        p=plan()
        self.assertEqual(core.geometry(p,p['trigger']+2*p['atr_1h']),'LATE_CHASE')


class MeasurementTests(unittest.TestCase):
    def p(self,side='LONG'):
        return dict(direction=side,stop=97. if side=='LONG' else 103.,atr_1h=2.)

    def test_stop_then_rally_never_becomes_a_profitable_plan(self):
        p=self.p();g=outcomes.initial(p,START,100.)
        g=outcomes.advance(p,g,[minute(START,100,120,96,99),minute(START+M,99,150,98,145)])
        r=outcomes.result(p,g)
        self.assertAlmostEqual(r['fee_slippage_only_pct'],-3.3)
        self.assertEqual(r['raw_mfe_pct'],50.)
        self.assertEqual(r['in_plan_mfe_lower_pct'],0.)
        self.assertAlmostEqual(r['in_plan_mfe_upper_pct'],20.)
        self.assertNotIn('10',r['in_plan_reach'])
        self.assertIn('10',r['raw_reach']);self.assertIsNone(r['full_net_pct'])

    def test_gap_pauses_and_repairs_without_skipping(self):
        p=self.p();g=outcomes.initial(p,START,100.)
        g=outcomes.advance(p,g,[minute(START+M)])
        self.assertEqual(g['cursor_ms'],START);self.assertEqual(g['missing_ms'],START)
        self.assertIsNone(outcomes.result(p,g)['fee_slippage_only_pct'])
        self.assertAlmostEqual(outcomes.result(p,g)['missing_assumed_stop_fee_only_pct'],-3.3)
        g=outcomes.advance(p,g,[minute(START),minute(START+M)])
        self.assertIsNone(g['missing_ms']);self.assertEqual(g['cursor_ms'],START+2*M)

    def test_gap_open_executes_worse_than_stop_short_and_long(self):
        for side,b,expected in [('LONG',minute(START,90,92,89,91),-10.3),
                                ('SHORT',minute(START,110,112,109,111),-10.3)]:
            p=self.p(side);g=outcomes.advance(p,outcomes.initial(p,START,100.),[b])
            self.assertAlmostEqual(outcomes.result(p,g)['fee_slippage_only_pct'],expected)

    def test_known_funding_uses_sign_mark_and_verified_coverage(self):
        p=self.p('SHORT');g=outcomes.initial(p,START,100.)
        g=outcomes.advance(p,g,[minute(START,100,101,99,100),minute(START+M,100,104,99,103)])
        funding=[dict(time_ms=START,rate=-.001,mark_price=100.)]
        r=outcomes.result(p,g,funding,(START,START+2*M))
        self.assertAlmostEqual(r['funding_cost_pct'],.1)
        self.assertAlmostEqual(r['full_net_pct'],-3.4)
        self.assertIsNone(outcomes.result(p,g,funding)['full_net_pct'])

    def test_trailing_only_changes_next_minute_after_complete_four_hours(self):
        p=self.p();g=outcomes.initial(p,START,100.)
        path=[minute(START+i*M,100,107,99,100) for i in range(240)]
        before=outcomes.advance(p,g,path[:-1]);self.assertEqual(before['stop'],97.)
        after=outcomes.advance(p,before,path[-1:]);self.assertEqual(after['stop'],98.5)
        self.assertIsNone(after['exit'])

    def test_open_plans_are_included_in_horizon_measurement(self):
        p=self.p();g=outcomes.initial(p,START,100.)
        g=outcomes.advance(p,g,[minute(START+i*M,100,101,99,100.5) for i in range(1440)])
        cp=g['checkpoints']['24'];self.assertTrue(cp['open_plan'])
        self.assertAlmostEqual(cp['fee_slippage_only_pct'],.2)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'LS_STATE_DIR':self.tmp.name,'SWING_NOTIFICATIONS':'0'})
        self.env.start();self.c=runtime.connect()

    def tearDown(self):self.c.close();self.env.stop();self.tmp.cleanup()

    def test_episode_dedup_survives_restart_and_requires_six_hour_reset(self):
        p=plan();eid=runtime.admit_episode(self.c,p,bars());self.c.commit()
        self.c.close();self.c=runtime.connect()
        self.assertIsNone(runtime.admit_episode(self.c,p,bars()))
        r=bars()
        for i in range(7):
            b=copy.deepcopy(r[-1]);b.update(open_ms=r[-1]['open_ms']+H,close_ms=r[-1]['close_ms']+H,
                                         close=100. if i<6 else 101.3);r.append(b)
        later=dict(p,confirmation_ms=r[-1]['close_ms'])
        new=runtime.admit_episode(self.c,later,r)
        self.assertNotEqual(eid,new);self.assertIsNotNone(new)

    def test_reference_is_strictly_after_decision_or_ack(self):
        runtime.reference(self.c,'e',plan(),'DELIVERED',START+1234)
        r=self.c.execute('SELECT * FROM references_v1').fetchone()
        self.assertEqual(r['reference_ms'],START+M)
        self.assertEqual(r['state'],'WAIT_ENTRY')

    def test_paper_default_never_calls_sender(self):
        sender=Mock();p=plan()
        self.assertEqual(runtime.publish(self.c,None,'e',p,p['observed_ms'],sender),'PAPER_ONLY')
        sender.assert_not_called()

    def test_uncertain_delivery_is_not_retried(self):
        sender=Mock(side_effect=TimeoutError());p=plan()
        with patch.dict(os.environ,{'SWING_NOTIFICATIONS':'1','TELEGRAM_BOT_TOKEN':'test','TELEGRAM_CHAT_ID':'123'}),patch.object(runtime,'quote',return_value=p['price']):
            self.assertEqual(runtime.publish(self.c,None,'e',p,p['observed_ms'],sender),'DELIVERY_UNCERTAIN')
            self.assertEqual(runtime.publish(self.c,None,'e',p,p['observed_ms'],sender),'ALREADY_ATTEMPTED')
        self.assertEqual(sender.call_count,1)

    def test_single_explicit_profile_excludes_major(self):
        import long_short_runtime_profile as profile
        with patch.dict(os.environ,{'LS_RUNTIME_PROFILE':'SWING_STRUCTURE_V1','LS_MAJOR_MOVE_V1':'1'}):
            self.assertTrue(profile.enabled());self.assertFalse(profile.major_enabled())
            self.assertEqual([n for n,_ in profile.SWING_WORKERS if 'swing' in n],['swing_structure'])
            self.assertNotIn('major_move',[n for n,_ in profile.SWING_WORKERS])

    def test_actual_collector_schema_drives_scan_entry_and_outcome(self):
        from long_short_futures_kline_shadow import initialize,SOURCE
        path=Path(self.tmp.name)/'binance_futures_kline_shadow.db'
        k=initialize(path);k.row_factory=sqlite3.Row
        r=bars();now=r[-1]['close_ms']+1000
        for b in r:
            k.execute('INSERT INTO futures_closed_klines(symbol,interval,open_ms,close_ms,open,high,low,close,volume,source,received_ms,quote_volume,trade_count,taker_buy_quote) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                      ('TESTUSDT','1h',b['open_ms'],b['close_ms'],b['open'],b['high'],b['low'],b['close'],1000.,SOURCE,now,b['quote_volume'],100,b['taker_buy_quote']))
        k.commit()
        with patch.object(runtime,'universe',return_value=[('TESTUSDT',3e6,101.2,2.)]):
            coverage=runtime.scan(self.c,k,now)
        self.assertEqual(coverage['counts']['BASE_BREAKOUT_LONG'],1)
        ref=self.c.execute('SELECT * FROM references_v1').fetchone()
        self.assertEqual(ref['kind'],'RESEARCH');start=ref['reference_ms']
        for i in range(2):
            b=minute(start+i*M,101.2,101.3,101.,101.1)
            k.execute('INSERT INTO futures_closed_klines(symbol,interval,open_ms,close_ms,open,high,low,close,volume,source,received_ms,quote_volume,trade_count,taker_buy_quote) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                      ('TESTUSDT','1m',b['open_ms'],b['close_ms'],b['open'],b['high'],b['low'],b['close'],1000.,SOURCE,start+2*M,1e5,100,5e4))
        k.commit();runtime.measure(self.c,k,start+2*M+1000)
        ref=self.c.execute('SELECT * FROM references_v1').fetchone();g=json.loads(ref['progress'])
        self.assertEqual(ref['state'],'TRACKING');self.assertEqual(g['entry_ms'],start)
        self.assertEqual(g['cursor_ms'],start+2*M)
        summary=runtime.report(self.c)['cohorts']['RESEARCH']
        self.assertEqual(summary['total'],1);self.assertEqual(summary['full_net_known_count'],0)
        k.close()

    def test_six_attempt_budget_counts_uncertain_delivery(self):
        p=plan();now=p['observed_ms'];sender=Mock()
        for i in range(6):self.c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',(str(i),now,None,'DELIVERY_UNCERTAIN','{}'))
        with patch.dict(os.environ,{'SWING_NOTIFICATIONS':'1','TELEGRAM_BOT_TOKEN':'test','TELEGRAM_CHAT_ID':'123'}):
            self.assertEqual(runtime.publish(self.c,None,'seventh',p,now,sender),'DAILY_NOTICE_BUDGET')
        sender.assert_not_called()

    def test_data_gap_after_exit_does_not_reclassify_closed_profit(self):
        p=dict(direction='LONG',stop=97.,atr_1h=2.)
        g=outcomes.initial(p,START,100.);g['stop']=104.
        g=outcomes.advance(p,g,[minute(START,105,106,103,104)])
        fee=outcomes.result(p,g)['fee_slippage_only_pct']
        g=outcomes.advance(p,g,[minute(START+2*M)])
        self.assertEqual(outcomes.result(p,g)['missing_assumed_stop_fee_only_pct'],fee)


if __name__=='__main__':unittest.main()
