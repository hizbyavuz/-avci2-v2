import copy
import unittest
from long_short_structural_plan_v3 import rebuild, connected_bands


def zone(lo,hi):
    return dict(low=lo,high=hi,side='RESISTANCE',center=(lo+hi)/2,
                timeframe='15m',touches=2,strength=2,tolerance=.1)


class GeometryTests(unittest.TestCase):
    def run_plan(self,direction='LONG',zones=None):
        p=dict(direction=direction,trigger_level=100,invalidation=98 if direction=='LONG' else 102,
               target1=100.01 if direction=='LONG' else 99.99,target2=0,
               retest_low=99.5,retest_high=100.5,setup_type='BREAKOUT')
        g=dict(level_ok=True,trigger_zone=zone(99.9,100.1),qualified_precheck=False)
        zs=zones or [zone(99.9,100.1),zone(100.05,100.3),zone(104,104.2),zone(95,95.2)]
        before=copy.deepcopy((p,g,zs))
        out=rebuild(p,g,zs,.3,1,.9)
        self.assertEqual((p,g,zs),before)
        return out

    def test_long_clears_connected_band_and_preserves_stop(self):
        p,g=self.run_plan()
        self.assertEqual(p['trigger_level'],100.3)
        self.assertEqual(p['target1'],104)
        self.assertEqual(p['invalidation'],98)
        self.assertTrue(g['qualified_precheck'])
        self.assertAlmostEqual(g['room_pct'],(104/100.3-1)*100)

    def test_short_linear_return_and_front_edge(self):
        p,g=self.run_plan('SHORT')
        self.assertEqual(p['trigger_level'],99.9)
        self.assertEqual(p['target1'],95.2)
        self.assertEqual(p['invalidation'],102)
        self.assertAlmostEqual(g['room_pct'],(1-95.2/99.9)*100)

    def test_near_obstacle_never_skipped_to_manufacture_reward(self):
        p,g=self.run_plan(zones=[zone(99.9,100.1),zone(100.5,100.6),zone(110,111)])
        self.assertEqual(p['target1'],100.5)
        self.assertFalse(g['qualified_precheck'])

    def test_unknown_target_fails_closed(self):
        p,g=self.run_plan(zones=[zone(99.9,100.1)])
        self.assertFalse(g['qualified_precheck'])
        self.assertEqual(g['structural_plan_v3']['reason'],'NO_OBSERVED_TARGET')

    def test_transitive_overlap(self):
        self.assertEqual(len(connected_bands([zone(100,101),zone(102,103),zone(100.9,102.1)])),1)

    def test_nonfinite_zone_rejected(self):
        with self.assertRaises(ValueError):connected_bands([zone(1,float('inf'))])

    def test_live_gate_rechecks_remaining_room_at_actual_price(self):
        import json
        from unittest.mock import patch
        import long_short_live_pool as lp
        row=dict(direction='LONG',data_mode='BINANCE_FUTURES',derivatives_provider='BINANCE_FUTURES',
                 invalidation=99,target1=101,structure_gate_json=json.dumps({
                     'structural_plan_v3':{'version':'v3'},'required_room_pct':.9}))
        book=dict(available=True,source='BINANCE_FUTURES_BOOK',notional_usdt=250,
                  costs={'250':{'buy_bps':10,'sell_bps':10}})
        with patch.dict('os.environ',{'LS_NATIVE_CHAIN_V2':'1'}),patch.object(lp,'EXECUTION_MIN_NET_T1_R',.1):
            r=lp.live_execution_gate(row,100.2,book)
        self.assertGreater(r['net_t1_r'],.1)
        self.assertFalse(r['qualified'])

    def test_audit_uses_active_database_not_retired_default(self):
        from unittest.mock import patch
        from long_short_operational_audit import active_database_audit
        dbs=[{'file':'long_short_analyst.db','count':0},{'file':'long_short_analyst_v2.db','count':10}]
        with patch.dict('os.environ',{'LS_DB':'/app/state/long_short_analyst_v2.db'}):
            row,name=active_database_audit(dbs,'LS_DB','long_short_analyst.db')
        self.assertEqual(row['count'],10)
