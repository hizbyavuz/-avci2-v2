import copy
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch
import long_short_analyst as analyst
from long_short_structural_plan_v7 import rebuild
from long_short_structural_plan_v5 import rebuild as previous
import test_local_geometry_continuity_v2 as fixtures
from test_local_geometry_continuity_v2 import zone

class LocalLevelIdentityTests(unittest.TestCase):
    def test_same_htf_level_does_not_move_local_entry_in_either_direction(self):
        for side in ('LONG','SHORT'):
            p,g,z=fixtures.LocalGeometryTests().inputs(side)
            z.append(zone(98.5,101.5,'RESISTANCE' if side=='LONG' else 'SUPPORT','4h'))
            frozen=copy.deepcopy((p,g,z))
            old,og=previous(p,g,z,.3,1,.9)
            new,ng=rebuild(p,g,z,.3,1,.9)
            self.assertEqual((p,g,z),frozen)
            self.assertEqual(og['structural_plan_v3']['reason'],'COMMON_REGION_TOO_WIDE')
            self.assertTrue(ng['qualified_precheck'])
            self.assertAlmostEqual(new['trigger_level'],100.1 if side=='LONG' else 99.9)
            self.assertEqual(new['target1'],104 if side=='LONG' else 96)
            self.assertEqual(new['invalidation'],p['invalidation'])
            self.assertEqual(len(ng['structural_plan_v3']['same_level_context']),1)

    def test_distinct_htf_obstacle_and_close_target_still_block(self):
        for side in ('LONG','SHORT'):
            p,g,z=fixtures.LocalGeometryTests().inputs(side)
            z.append(zone(99,110) if side=='LONG' else zone(90,101,'SUPPORT'))
            z[-1]['timeframe']='4h'
            _,ng=rebuild(p,g,z,.3,1,.9)
            self.assertFalse(ng['qualified_precheck'])
            self.assertEqual(ng['structural_plan_v3']['reason'],'ENTRY_INSIDE_OPPOSING_ZONE')
            p,g,z=fixtures.LocalGeometryTests().inputs(side)
            z[1]=zone(100.4,100.6) if side=='LONG' else zone(99.4,99.6,'SUPPORT')
            _,ng=rebuild(p,g,z,.3,1,.9)
            self.assertFalse(ng['qualified_precheck'])

    def test_failed_plan_clears_actionable_target_and_metrics(self):
        p,g,z=fixtures.LocalGeometryTests().inputs()
        new,ng=rebuild(p,g,z[:1],.3,1,.9)
        self.assertEqual(new['target1'],0)
        self.assertIsNone(ng['net_t1_r']);self.assertIsNone(ng['room_pct'])

    def test_captured_pump_keeps_independent_obstacles(self):
        d=json.loads((Path(__file__).parent/'fixtures/pump_geometry_20261015.json').read_text())
        p,g=rebuild(d['plan'],d['gate'],d['zones'],d['cost_pct'],d['min_net_r'],d['required_room_pct'])
        self.assertLess(abs(p['trigger_level']/d['plan']['trigger_level']-1)*100,.3)
        self.assertFalse(g['qualified_precheck'])

    def test_broad_prefilter_uses_verified_universe_without_direction_override(self):
        rows=[('AAAUSDT',30e6,1,0),('ZZZUSDT',40e6,1,0)]
        with patch.dict(os.environ,{'LS_BINANCE_FUTURES_ONLY':'1','LS_NATIVE_BROAD_PREFILTER_V1':'1'}), \
             patch.object(analyst,'native_v2_enabled',return_value=True), \
             patch.object(analyst,'native_futures_websocket_universe',return_value=rows) as native, \
             patch.object(analyst,'universe') as old:
            self.assertEqual(analyst.discovery_universe(),rows)
            native.assert_called_once_with(all_eligible=True);old.assert_not_called()

    def test_broad_prefilter_does_not_change_other_source_modes(self):
        with patch.object(analyst,'native_v2_enabled',return_value=False),patch.object(analyst,'universe',return_value=[]) as old:
            self.assertEqual(analyst.discovery_universe(),[]);old.assert_called_once()
