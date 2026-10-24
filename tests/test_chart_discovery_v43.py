import contextlib
import copy
import io
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock,patch

import long_short_chart_first as chart
import long_short_chart_first_worker as worker
from long_short_chart_discovery_audit import early_observation,market_position,short_flow_aligned
from long_short_chart_setups import setups
from long_short_chart_flow import hourly_flow
from test_chart_first import sample,aligned_flow,NOW
import test_chart_flow_reversal as flow_fixture


def turn_flow(short=False):
    flow=aligned_flow(short);end=flow['end_ms']
    flow['recent_windows']={'15':dict(start_ms=end-900000,end_ms=end,buy_share=.4 if short else .6,
        quote_volume=50000,price_return_pct=-.5 if short else .5)}
    return flow


class DiscoveryTests(unittest.TestCase):
    def test_good_watch_is_not_hidden_by_first_weak_watch_both_sides(self):
        for short in (False,True):
            f,price=sample(short);side='SHORT' if short else 'LONG';sign=-1 if short else 1
            h,l=chart.pivots(f['5m']);pattern=setups(f['5m'],side,h,l,chart.CONFIG)[0]
            good=dict(pattern,model='GOOD',model_ready=False,confirmation_ms=None)
            bad=dict(good,model='WEAK_FIRST',stop=price-sign*3)
            with patch.object(chart,'setups',return_value=[bad,good]),patch.object(chart,'level_watch',return_value=None),patch.object(chart,'first_obstacle',return_value=(price+sign*1,'15m')):
                p=chart.assess('X',f,price,50e6,NOW,flow=aligned_flow(short))
            self.assertEqual(p['model'],'GOOD');self.assertEqual(p['stage'],'WATCH')
            self.assertIn('READY_REWARD_BELOW_RISK',p['pattern_evaluations'][0]['ready_blockers'])
            self.assertFalse(p['ready_blockers'])

    def fixture(self,short=False):
        f,price=sample(short);opp,_=sample(not short);f['1h']=opp['1h']
        flow=turn_flow(short)
        base=chart.assess('XUSDT',f,price,50e6,NOW,flow=flow)
        return base,f,price,flow

    def test_early_turn_observed_without_reversing_production_direction(self):
        for short in (False,True):
            base,f,price,flow=self.fixture(short);saved=copy.deepcopy(base)
            p=early_observation(base,f,NOW)
            self.assertIsNotNone(p)
            self.assertEqual(p['direction'],'SHORT' if short else 'LONG')
            self.assertEqual(p['stage'],'EARLY_OBSERVATION')
            self.assertFalse(p['telegram_eligible'])
            self.assertNotIn(base['stage'],('WATCH','READY'))
            self.assertEqual(base,saved)

    def test_missing_flow_or_already_aligned_hour_is_not_an_early_turn(self):
        base,f,_,_=self.fixture()
        for change in ({'hourly_flow':{}},{'context':{}},
                       {'hourly_flow':dict(base['hourly_flow'],recent_windows={})}):
            self.assertIsNone(early_observation(dict(base,**change),f,NOW))
        same=copy.deepcopy(base);same['context']['1h']['direction']='UP'
        self.assertIsNone(early_observation(same,f,NOW))
        self.assertFalse(short_flow_aligned(base['hourly_flow'],'LONG',NOW+300000))

    def test_early_turn_keeps_cost_and_chase_guards(self):
        base,f,price,_=self.fixture()
        with patch.object(chart,'first_obstacle',return_value=(price+.01,'15m')):
            self.assertIsNone(early_observation(base,f,NOW))
        self.assertIsNone(early_observation(dict(base,price=price+5),f,NOW))

    def test_recent_windows_are_weighted_closed_data(self):
        c,end=flow_fixture.FlowReversalTests().flow_db()
        flow=hourly_flow(c,'X',NOW)
        self.assertAlmostEqual(flow['recent_windows']['15']['buy_share'],.55)
        self.assertEqual(flow['recent_windows']['15']['start_ms'],end-900000)
        self.assertEqual(flow['recent_windows']['30']['quote_volume'],1200)
        self.assertAlmostEqual(flow['recent_windows']['30']['price_return_pct'],1)
        self.assertAlmostEqual(flow['buy_share'],.6)
        c.close()

    def test_position_is_descriptive_and_does_not_clamp_breakouts(self):
        f,p=sample();high=max(r[2] for r in f['1h'][-24:])
        saved=copy.deepcopy(f);m=market_position(f,high+1)
        self.assertGreater(m['position_in_24h_range_pct'],100)
        self.assertLess(m['distance_to_24h_high_pct'],0)
        self.assertEqual(saved,f)

    def test_current_outcomes_are_not_starved_by_old_missing_archives(self):
        with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{'LS_STATE_DIR':root}):
            c=worker.connect()
            for i in range(100):
                worker.record(c,dict(strategy_id='old',symbol=f'OLD{i}',observed_ms=60000,
                    direction='LONG',stage='DELIVERED_READY',reason='ACK',price=100,episode_id=str(i)))
            worker.record(c,dict(strategy_id=chart.STRATEGY_ID,symbol='CURRENT',observed_ms=NOW-20*60000,
                direction='LONG',stage='EARLY_OBSERVATION',reason='HOURLY_DIRECTION_NOT_YET_ALIGNED',price=100))
            c.commit();rows=worker.due_assessments(c,NOW)
            self.assertEqual(rows[0]['symbol'],'CURRENT');self.assertEqual(len(rows),90)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM assessments').fetchone()[0],101)
            c.close()

    def test_early_observation_keeps_price_path_when_coin_leaves_current_universe(self):
        import long_short_runtime_profile as profile
        with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{'LS_STATE_DIR':root}),patch('time.time',return_value=NOW/1000),patch('long_short_analyst.native_futures_websocket_universe',return_value=[]),contextlib.redirect_stdout(io.StringIO()):
            c=worker.connect()
            worker.record(c,dict(strategy_id=chart.STRATEGY_ID,symbol='EARLYUSDT',observed_ms=NOW-60000,
                direction='LONG',stage='EARLY_OBSERVATION',reason='HOURLY_DIRECTION_NOT_YET_ALIGNED',price=100))
            c.commit();c.close()
            requested,urgent=profile.demand(limit=166,full_chart=True)
            self.assertIn('EARLYUSDT',requested);self.assertIn('EARLYUSDT',urgent)

    def test_live_scan_records_observer_but_never_publishes_or_creates_episode(self):
        base,frames,price,flow=self.fixture()
        with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{'LS_STATE_DIR':root,'LS_CHART_FIRST_V1':'1','TELEGRAM_BOT_TOKEN':'test'}),patch('time.time',return_value=NOW/1000),contextlib.redirect_stdout(io.StringIO()),patch('long_short_analyst.native_futures_websocket_universe',return_value=[('XUSDT',50e6,price,1)]),patch('long_short_analyst.resolve_telegram_chat',return_value='123'),patch('long_short_message_card.publish') as publish,patch.object(worker,'candles',return_value=(frames,{})),patch.object(worker,'quote',return_value=price),patch.object(worker,'hourly_flow',return_value=flow):
            sqlite3.connect(root+'/binance_futures_kline_shadow.db').close()
            worker.scan()
            c=worker.connect()
            self.assertEqual(c.execute("SELECT COUNT(*) FROM assessments WHERE stage='EARLY_OBSERVATION'").fetchone()[0],1)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM episodes').fetchone()[0],0)
            publish.assert_not_called();c.close()


if __name__=='__main__':unittest.main()
