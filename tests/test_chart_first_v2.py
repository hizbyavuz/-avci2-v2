import copy
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch, Mock

from long_short_chart_first import CONFIG, VERSION, STRATEGY_ID, assess, first_obstacle, pivots, message
from long_short_chart_first_worker import connect, record, resolve, live_stage, advance, aggregate_closed_tail, candles
from long_short_chart_setups import setup, geometry, quality_limit
from long_short_chart_outcomes import outcome
from test_chart_first import sample, NOW


def bar(t, op, hi, lo, cl):
    return [t,op,hi,lo,cl,100,t+59999]


class ChartV2Tests(unittest.TestCase):
    def test_higher_timeframe_tail_requires_every_closed_five_minute_bar(self):
        rows=[[i*300000,100+i,102+i,99+i,101+i,10, (i+1)*300000-1] for i in range(48)]
        for period in (900000,3600000,14400000):
            result=aggregate_closed_tail(rows,period)
            self.assertEqual(result[0][0],0)
            self.assertEqual(result[0][6],period-1)
            count=period//300000
            self.assertEqual(result[0][2],101+count)
            self.assertEqual(result[0][5],10*count)
            missing=rows[:1]+rows[2:]
            self.assertNotIn(0,[r[0] for r in aggregate_closed_tail(missing,period)])
            partial=copy.deepcopy(rows);partial[1][6]-=1
            self.assertNotIn(0,[r[0] for r in aggregate_closed_tail(partial,period)])

    def test_native_reader_repairs_rest_lag_without_replacing_existing_bars(self):
        frames,_=sample();k=sqlite3.connect(':memory:')
        k.execute('CREATE TABLE futures_closed_klines(symbol,interval,open_ms,open,high,low,close,volume,close_ms,source)')
        expected_end=frames['15m'][-1][6]
        lagging=frames['15m'][:-1]
        older=lagging[0].copy();older[0]-=900000;older[6]-=900000
        frames['15m']=[older]+lagging
        original=frames['15m'][-1].copy()
        for tf,rows in frames.items():
            for r in rows:k.execute('INSERT INTO futures_closed_klines VALUES(?,?,?,?,?,?,?,?,?,?)',
                                   ('XUSDT',tf,*r,'BINANCE_UM_FUTURES_REST'))
        result,provenance=candles(k,'XUSDT',with_provenance=True)
        self.assertEqual(result['15m'][-1][6],expected_end)
        self.assertEqual(result['15m'][-2],original)
        self.assertEqual(provenance['15m'],1)
        self.assertEqual(len(result['15m']),120)
        k.close()

    def test_jto_geometry_is_not_an_acceptable_entry(self):
        g=geometry('SHORT',.4491,.4571,.4473,.30)
        self.assertAlmostEqual(g['net_room_pct'],.1008016032)
        self.assertLess(g['net_reward_risk'],CONFIG['min_net_reward_risk'])
        p=dict(version=VERSION,direction='SHORT',trigger=.4492,stop=.4571,
               obstacle=.4473,entry_limit=.448,stage='WATCH',created_ms=NOW)
        self.assertEqual(live_stage(p,.4491,.44915,NOW-8000,NOW,.4493),'EXPIRED')

    def test_entire_advertised_entry_range_meets_net_payoff_policy(self):
        for short in (False,True):
            frames,price=sample(short);p=assess('XUSDT',frames,price,50e6,NOW)
            self.assertEqual(p['stage'],'READY',p)
            for fraction in (0,.25,.5,.75,1):
                entry=p['trigger']+fraction*(p['entry_limit']-p['trigger'])
                g=geometry(p['direction'],entry,p['stop'],p['obstacle'],.30)
                self.assertGreaterEqual(g['net_reward_risk']+1e-10,1)
            limit=quality_limit(p['direction'],p['stop'],p['obstacle'],.3,1)
            self.assertAlmostEqual(geometry(p['direction'],limit,p['stop'],p['obstacle'],.3)['net_reward_risk'],1)

    def test_failed_break_does_not_erase_old_resistance(self):
        rows=[bar(i*60000,*v) for i,v in enumerate([
            (98,99,97,98),(98,100,97.5,99),(99,101,98,100),
            (100,100.5,99,100),(100,100.6,99,100),
            (100,103,99.5,102),(102,102.5,100,101.5),
            (101.5,102,99.8,100.3),(100.3,100.6,100,100.4)])]
        self.assertEqual(first_obstacle({'5m':rows},'LONG',100.2,.5)[0],101)
        mirrored=[[r[0],200-r[1],200-r[3],200-r[2],200-r[4],r[5],r[6]] for r in rows]
        self.assertEqual(first_obstacle({'5m':mirrored},'SHORT',99.8,.5)[0],99)

    def test_crossed_first_obstacle_is_not_replaced_by_farther_one(self):
        frames,price=sample();p=assess('XUSDT',frames,price,50e6,NOW)
        p.update(created_ms=NOW,stage='WATCH')
        self.assertEqual(live_stage(p,p['obstacle']+.01,p['trigger']+.01,NOW-8000,NOW),'OBSTACLE_REACHED')

    def test_old_closed_break_is_not_a_new_trigger(self):
        frames,price=sample();p=assess('XUSDT',frames,price,50e6,NOW)
        p.update(created_ms=NOW,stage='WATCH')
        self.assertEqual(live_stage(p,price,price,NOW-8000,NOW,price),'WATCH')

    def test_pullback_requires_higher_low_and_actual_recovery_both_sides(self):
        # Narrow earlier candles prevent this pullback being classified as compression.
        rows=[bar(i*60000,98,98.2,97.8,98) for i in range(30)]
        values=[(100,102,99,101),(101,104,100,103),(103,103.5,101,102),
                (102,102.2,100.5,101),(101,101.7,100.8,101.6),
                (101.6,102.6,101.4,102.4)]
        rows.extend(bar((30+i)*60000,*v) for i,v in enumerate(values))
        hi=[(31*60000,104)];lo=[(28*60000,99)]
        for short in (False,True):
            rr=rows if not short else [[r[0],200-r[1],200-r[3],200-r[2],200-r[4],r[5],r[6]] for r in rows]
            hh,ll=(hi,lo) if not short else ([(t,200-v) for t,v in lo],[(t,200-v) for t,v in hi])
            p=setup(rr,'SHORT' if short else 'LONG',hh,ll,CONFIG)
            self.assertIsNotNone(p)
            self.assertEqual(p['model'],'PULLBACK_CONTINUATION')
            self.assertTrue(p['model_ready'])
        bad=copy.deepcopy(rows);bad[33][3]=98.9
        self.assertIsNone(setup(bad,'LONG',hi,lo,CONFIG))

    def test_unknown_obstacle_does_not_create_fabricated_target(self):
        self.assertFalse(geometry('LONG',100,99,None,.3)['valid'])

    def test_messages_explain_cost_and_structural_distance(self):
        f,price=sample();p=assess('XUSDT',f,price,50e6,NOW)
        text=message(p)
        self.assertIn('🔔 5 dk mum',text)
        self.assertIn('Stopa uzaklık',text)
        self.assertIn('masraf hariç',text)

    def test_partial_first_minute_hit_is_unresolved_not_skipped(self):
        p=dict(observed_ms=68000,price=100,direction='LONG',stop=99,obstacle=102,stage='DELIVERED_READY')
        bars=[bar(60000,100,103,99.5,100.5),bar(120000,100.5,100.8,100.2,100.4)]
        r=outcome(p,bars,1,.3)
        self.assertEqual(r['first_barrier_status'],'UNRESOLVED_BOUNDARY')
        self.assertNotIn('model_net_return_pct',r)
        self.assertIn('OBSTACLE',r['possible_first_minute_levels'])
        self.assertGreater(r['max_up_pct_upper'],r['max_up_pct_lower'])

    def test_complete_first_minute_stop_first_and_cost_model(self):
        p=dict(observed_ms=60000,price=100,direction='LONG',stop=99,obstacle=102,stage='DELIVERED_READY')
        r=outcome(p,[bar(60000,100,103,98,101)],1,.3)
        self.assertEqual(r['first_level'],'INVALIDATION')
        self.assertAlmostEqual(r['model_net_return_pct'],-1.3)
        self.assertTrue(r['same_minute_ambiguous'])

    def test_missing_first_minute_is_not_reported_as_complete(self):
        p=dict(observed_ms=68000,price=100,direction='SHORT',stage='REJECTED')
        with self.assertRaisesRegex(ValueError,'INCLUDING_FIRST_MINUTE'):
            outcome(p,[bar(120000,100,101,99,100)],1,.3)

    def test_watch_and_rejected_are_never_counted_as_executed_entries(self):
        for stage in ('WATCH','DELIVERED_WATCH','REJECTED'):
            p=dict(observed_ms=60000,price=100,direction='LONG',stop=99,obstacle=102,stage=stage)
            r=outcome(p,[bar(60000,100,102.5,99.5,102)],1,.3)
            self.assertNotIn('model_net_return_pct',r)

    def test_gap_stop_uses_observed_open_not_ideal_stop(self):
        p=dict(observed_ms=60000,price=100,direction='LONG',stop=99,obstacle=102,stage='DELIVERED_READY')
        r=outcome(p,[bar(60000,98,98.5,97.5,98)],1,.3)
        self.assertAlmostEqual(r['model_net_return_pct'],-2.3)

    def test_resolver_keeps_versions_separate_and_includes_first_minute(self):
        with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{'LS_STATE_DIR':root}):
            c=connect();k=sqlite3.connect(':memory:')
            k.execute('CREATE TABLE futures_closed_klines(symbol,interval,open_ms,open,high,low,close,volume,close_ms,source)')
            for i in range(17):
                r=bar((1+i)*60000,100,103 if i==0 else 100.5,99.5,100.1)
                k.execute('INSERT INTO futures_closed_klines VALUES(?,?,?,?,?,?,?,?,?,?)',
                          ('XUSDT','1m',*r,'BINANCE_UM_FUTURES_WEBSOCKET'))
            base=dict(version=VERSION,strategy_id=STRATEGY_ID,symbol='XUSDT',observed_ms=68000,
                price=100,direction='LONG',stage='DELIVERED_READY',reason='TEST',stop=99,obstacle=102)
            record(c,base);c.commit();resolve(c,k,1200000)
            r=c.execute('SELECT state,payload FROM outcomes WHERE horizon=15').fetchone()
            self.assertEqual(r[0],'UNRESOLVED_BOUNDARY')
            self.assertTrue(json.loads(r[1])['initial_partial_minute_included'])
            c.close();k.close()

    def test_existing_watch_can_trigger_without_a_new_setup_or_stale_support(self):
        with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{'LS_STATE_DIR':root}),patch('time.time',return_value=NOW/1000):
            c=connect();k=sqlite3.connect(':memory:')
            k.executescript('CREATE TABLE futures_last_price(symbol,price,event_ms,received_ms,source);'
                           'CREATE TABLE futures_closed_klines(symbol,interval,close,close_ms,open_ms);')
            frames,price=sample();p=assess('XUSDT',frames,price,50e6,NOW)
            p.update(stage='WATCH',created_ms=NOW-60000,support={'oi_change_1h':999})
            k.execute('INSERT INTO futures_last_price VALUES(?,?,?,?,?)',('XUSDT',price,NOW-100,NOW-100,'BINANCE_UM_FUTURES_WEBSOCKET'))
            k.executemany('INSERT INTO futures_closed_klines VALUES(?,?,?,?,?)',[
                ('XUSDT','5m',price,NOW-8000,NOW-308000),
                ('XUSDT','5m',p['trigger']-.01,NOW-308000,NOW-608000)])
            c.execute('INSERT INTO episodes VALUES(?,?,?,?,?,?,?,?)',
                      ('e','XUSDT','LONG',NOW-60000,'WATCH','WATCH',None,json.dumps(p)));c.commit()
            decision=dict(p,stage='REJECTED',reason='NO_DEFINED_SETUP',support={'oi_change_1h':None},
                          active_plan_obstacle=p['obstacle'])
            publisher=Mock(return_value={'status':'ACK'})
            advance(c,k,{'XUSDT':decision},NOW,publisher,'token','123')
            saved=json.loads(c.execute('SELECT payload FROM episodes').fetchone()[0])
            self.assertEqual(saved['stage'],'READY')
            self.assertIsNone(saved['support']['oi_change_1h'])
            self.assertEqual(saved['stop'],p['stop'])
            publisher.assert_called_once()
            c.close();k.close()


if __name__=='__main__':unittest.main()
