import copy
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import long_short_live_pool as live
import long_short_qualified_chain as routing
from long_short_structural_plan_v4 import rebuild, from_candles, VERSION
from long_short_structural_plan_v3 import rebuild as old_rebuild
import test_long_short_live_levels as fixtures


def zone(low, high, side='RESISTANCE', timeframe='5m'):
    return dict(low=low, high=high, center=(low+high)/2, side=side,
                timeframe=timeframe, touches=2, strength=2, tolerance=(high-low)/2)


class LocalGeometryTests(unittest.TestCase):
    def inputs(self, side='LONG'):
        long=side=='LONG'
        p=dict(direction=side, trigger_level=100, invalidation=98 if long else 102,
               retest_low=99.8 if long else 100, retest_high=100 if long else 100.2,
               target1=104 if long else 96, target2=0, setup_type='BREAKOUT')
        a=zone(99.9,100.1, 'RESISTANCE' if long else 'SUPPORT')
        g=dict(trigger_zone=a, level_ok=True, qualified_precheck=True,
               thresholds={'level_near_pct':.8})
        z=[a, zone(104,104.2) if long else zone(95.8,96,'SUPPORT')]
        return p,g,z

    def test_local_entry_preserves_stop_and_retest_width_both_sides(self):
        for side in ('LONG','SHORT'):
            p,g,z=self.inputs(side); before=copy.deepcopy((p,g,z))
            new,gate=rebuild(p,g,z,.3,1,.9)
            self.assertEqual((p,g,z),before)
            self.assertEqual(new['invalidation'],p['invalidation'])
            self.assertAlmostEqual(new['retest_high']-new['retest_low'],.2)
            self.assertEqual(new['geometry_version'],VERSION)
            self.assertTrue(gate['qualified_precheck'])
            self.assertAlmostEqual(new['trigger_level'],100.1 if side=='LONG' else 99.9)

    def test_transitive_chain_is_blocking_obstacle_not_farther_entry(self):
        p,g,z=self.inputs()
        z=[zone(99.9,100.1),zone(100.05,100.4),zone(100.3,101),zone(104,104.2)]
        old,_=old_rebuild(p,g,z,.3,1,.9)
        new,gate=rebuild(p,g,z,.3,1,.9)
        self.assertEqual(old['trigger_level'],101)
        self.assertEqual(new['trigger_level'],100.1)
        self.assertFalse(gate['qualified_precheck'])
        self.assertEqual(gate['structural_plan_v3']['reason'],'ENTRY_INSIDE_OPPOSING_ZONE')

    def test_wide_htf_zone_never_moves_entry_or_disappears(self):
        p,g,z=self.inputs();z.append(zone(99,110,timeframe='4h'))
        new,gate=rebuild(p,g,z,.3,1,.9)
        self.assertEqual(new['trigger_level'],100.1)
        self.assertFalse(gate['qualified_precheck'])
        self.assertEqual(gate['next_opposing_zone']['timeframe'],'4h')

    def test_same_local_pivot_on_two_frames_is_not_its_own_target(self):
        p,g,z=self.inputs();z.append(zone(99.8,100.2,timeframe='15m'))
        new,gate=rebuild(p,g,z,.3,1,.9)
        self.assertEqual(new['trigger_level'],100.2)
        self.assertEqual(new['target1'],104)
        self.assertTrue(gate['qualified_precheck'])
        members=gate['structural_plan_v3']['trigger_band']['members']
        self.assertEqual(len(members),2)
        self.assertTrue(all(x['low']<=100<=x['high'] for x in members))

    def test_local_confluence_cannot_grow_transitively(self):
        p,g,z=self.inputs()
        z.extend([zone(99.8,100.2,timeframe='15m'),zone(100.15,100.5)])
        new,gate=rebuild(p,g,z,.3,1,.9)
        self.assertEqual(new['trigger_level'],100.2)
        self.assertEqual(len(gate['structural_plan_v3']['trigger_band']['members']),2)
        self.assertFalse(gate['qualified_precheck'])

    def test_opposite_side_zone_cannot_expand_trigger(self):
        p,g,z=self.inputs();z.append(zone(99,103,side='SUPPORT'))
        new,gate=rebuild(p,g,z,.3,1,.9)
        self.assertEqual(new['trigger_level'],100.1)
        self.assertEqual(new['target1'],104)

    def test_near_target_and_weak_local_level_remain_blocked(self):
        p,g,z=self.inputs();z[1]=zone(100.5,100.6)
        new,gate=rebuild(p,g,z,.3,1,.9)
        self.assertEqual(new['target1'],100.5)
        self.assertFalse(gate['qualified_precheck'])
        p,g,z=self.inputs();z[0].update(touches=1,strength=.75)
        _,gate=rebuild(p,g,z,.3,1,.9)
        self.assertFalse(gate['qualified_precheck'])

    def test_no_local_anchor_does_not_use_a_strong_htf_anchor(self):
        p,g,z=self.inputs();z[0]['timeframe']='4h'
        new,gate=rebuild(p,g,z,.3,1,.9)
        self.assertFalse(gate['qualified_precheck'])
        self.assertEqual(gate['structural_plan_v3']['reason'],'NO_LOCAL_TRIGGER_ZONE')

    def test_missing_target_and_bad_numeric_inputs_fail_closed(self):
        p,g,z=self.inputs()
        for zs in (z[:1], [dict(z[0],high=float('inf'))]):
            _,gate=rebuild(p,g,zs,.3,1,.9)
            self.assertFalse(gate['qualified_precheck'])
        for bad in (float('nan'),-1):
            _,gate=rebuild(p,g,z,bad,1,.9)
            self.assertFalse(gate['qualified_precheck'])

    def test_price_scale_does_not_change_risk_reward(self):
        p,g,z=self.inputs();_,a=rebuild(p,g,z,.3,1,.9)
        for k in ('trigger_level','invalidation','retest_low','retest_high','target1'):p[k]*=.0001
        for x in z:
            for k in ('low','high','center','tolerance'):x[k]*=.0001
        _,b=rebuild(p,g,z,.3,1,.9)
        self.assertAlmostEqual(a['net_t1_r'],b['net_t1_r'])

    def test_paired_comparison_uses_identical_zone_inputs(self):
        p,g,z=self.inputs()
        _,gate=from_candles(p,g,[(None,'5m',1)],100,lambda *args:z,.3,1,.9)
        compare=gate['geometry_comparison']
        self.assertTrue(compare['same_closed_candles'])
        self.assertFalse(compare['pnl_comparison'])
        self.assertNotEqual(compare['v3']['version'],compare['v4']['version'])

    def test_captured_pump_chain_no_longer_moves_entry_three_percent(self):
        data=json.loads((Path(__file__).parent/'fixtures/pump_geometry_20261015.json').read_text())
        args=(data['plan'],data['gate'],data['zones'],data['cost_pct'],data['min_net_r'],data['required_room_pct'])
        old,_=old_rebuild(*args);new,gate=rebuild(*args)
        self.assertAlmostEqual(old['trigger_level'],data['expected_v3_entry'])
        self.assertLess(abs(new['trigger_level']/data['plan']['trigger_level']-1)*100, .3)
        self.assertAlmostEqual(new['retest_high']-new['retest_low'],
                               data['plan']['retest_high']-data['plan']['retest_low'])
        self.assertFalse(gate['qualified_precheck'])
        self.assertEqual(gate['structural_plan_v3']['reason'],'ENTRY_INSIDE_OPPOSING_ZONE')


class ContinuityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=str(Path(self.temp.name)/'live.db')
        self.patch=patch.object(live,'LIVE_DB',self.path);self.patch.start()
        live.init_db()
        self.item=fixtures.LiveLifecycleTests()._item()
        self.item['scan_time']=(datetime.now(timezone.utc)-timedelta(minutes=6)).isoformat()
        live.sync_watchlist([self.item],reviewed_symbols={'TESTUSDT'})

    def tearDown(self):
        self.patch.stop();self.temp.cleanup()

    def row(self):
        with sqlite3.connect(self.path) as c:
            c.row_factory=sqlite3.Row
            return c.execute('SELECT * FROM watch_state').fetchone()

    def test_rotation_keeps_plan_and_qualified_confirmation(self):
        with sqlite3.connect(self.path) as c:c.execute("UPDATE watch_state SET stage='CLOSE_CONFIRMED'")
        before=self.row()
        live.sync_watchlist([],reviewed_symbols={'OTHERUSDT'})
        row=self.row()
        self.assertEqual(row['analyst_active'],1)
        self.assertEqual(row['analyst_review_deadline_utc'],before['analyst_review_deadline_utc'])
        self.assertEqual(row['last_actionable_scan_time'],before['last_actionable_scan_time'])
        quality={'qualified':True,'acceptance_bars_beyond_last3':2}
        self.assertEqual(live.next_stage(row,100.5,100.5,quality),'TRIGGERED')

    def test_explicit_rejection_or_radar_downgrade_pauses(self):
        live.sync_watchlist([],reviewed_symbols={'TESTUSDT'})
        self.assertFalse(live._row_is_analyst_active(self.row()))
        live.sync_watchlist([],reviewed_symbols={'OTHERUSDT'})
        self.assertEqual(self.row()['analyst_active'],0)
        live.sync_watchlist([self.item],reviewed_symbols={'TESTUSDT'})
        radar=copy.deepcopy(self.item);radar['radar_only']=True
        live.sync_watchlist([radar],reviewed_symbols={'TESTUSDT'})
        self.assertFalse(live._row_is_analyst_active(self.row()))

    def test_expiry_without_new_scan_blocks_trigger(self):
        expired=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
        with sqlite3.connect(self.path) as c:
            c.execute("UPDATE watch_state SET stage='CLOSE_CONFIRMED',analyst_review_deadline_utc=?",(expired,))
        quality={'qualified':True,'acceptance_bars_beyond_last3':2}
        self.assertEqual(live.next_stage(self.row(),100.5,100.5,quality),'CLOSE_CONFIRMED')
        live.sync_watchlist([],reviewed_symbols={'OTHERUSDT'})
        self.assertFalse(live._row_is_analyst_active(self.row()))

    def test_refresh_updates_deadline_not_locked_prices(self):
        before=self.row();new=copy.deepcopy(self.item)
        new.update(scan_time=live.now_iso(),trigger_level=101,target1=105)
        live.sync_watchlist([new],reviewed_symbols={'TESTUSDT'})
        row=self.row()
        self.assertGreater(row['analyst_review_deadline_utc'],before['analyst_review_deadline_utc'])
        self.assertEqual(row['trigger_level'],before['trigger_level'])
        self.assertEqual(row['target1'],before['target1'])

    def test_retained_candidate_gets_existing_priority_slots(self):
        with patch.dict('os.environ',{'LS_LIVE_DB':self.path}):
            self.assertEqual(routing.active_watch_priority(self.temp.name),['TESTUSDT'])
            output=routing.reserve(['TESTUSDT','A','B'],['A','B'],routing.active_watch_priority(self.temp.name),2,lambda x:x)
            self.assertEqual(output,['TESTUSDT','A'])
        live.sync_watchlist([],reviewed_symbols={'TESTUSDT'})
        with patch.dict('os.environ',{'LS_LIVE_DB':self.path}):
            self.assertEqual(routing.active_watch_priority(self.temp.name),[])

    def test_restart_reads_same_deadline_and_does_not_extend_it(self):
        deadline=self.row()['analyst_review_deadline_utc']
        live.init_db()
        live.sync_watchlist([],reviewed_symbols={'OTHERUSDT'})
        self.assertEqual(self.row()['analyst_review_deadline_utc'],deadline)

    def test_invalidation_still_overrides_retention(self):
        live.sync_watchlist([],reviewed_symbols={'OTHERUSDT'})
        self.assertEqual(live.next_stage(self.row(),98,98,{'qualified':True}),'INVALIDATED')


if __name__=='__main__':unittest.main()
