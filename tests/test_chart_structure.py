from test_chart_first import aligned_flow
import copy
import unittest
from unittest.mock import patch
import long_short_chart_first as chart
from long_short_chart_setups import level_watch
from test_chart_first import sample, NOW
import test_chart_selection


class StructureTests(unittest.TestCase):
    def test_micro_hurdle_is_visible_but_target_is_structural(self):
        f,price=sample()
        original=chart.first_obstacle
        def levels(frames,*args):
            if '5m' in frames:return (price+.05,'5m')
            return original(frames,*args)
        with patch.object(chart,'first_obstacle',side_effect=levels):
            old=chart.assess('X',f,price,50e6,NOW,comparison_v3=True,flow=aligned_flow(False))
            new=chart.assess('X',f,price,50e6,NOW,flow=aligned_flow(False))
        self.assertEqual(old['reason'],'OBSTACLE_WITHIN_COST_ALLOWANCE')
        self.assertEqual(new['stage'],'READY')
        self.assertNotEqual(new['obstacle_timeframe'],'5m')
        self.assertEqual(new['intermediate_level'],price+.05)
        self.assertIn('fiyat burada takılabilir',chart.message(new))
        self.assertGreaterEqual(new['geometry']['net_reward_risk'],1)

    def test_missing_structural_target_never_uses_farther_fabricated_target(self):
        f,price=sample()
        with patch.object(chart,'first_obstacle',return_value=(None,None)):
            p=chart.assess('X',f,price,50e6,NOW)
        self.assertEqual(p['stage'],'REJECTED')
        self.assertEqual(p['reason'],'FIRST_OBSTACLE_UNKNOWN')

    def test_level_watch_requires_closed_break_both_sides(self):
        rows=test_chart_selection.SelectionTests().fixture();rows[-1][4]=104
        for short in (False,True):
            rr=rows if not short else [[r[0],200-r[1],200-r[3],200-r[2],200-r[4],*r[5:]] for r in rows]
            h,l=chart.pivots(rr);side='SHORT' if short else 'LONG'
            watch=level_watch(rr,side,h,l,chart.CONFIG)
            self.assertIsNotNone(watch)
            self.assertFalse(watch['model_ready'])
            self.assertIsNone(watch['confirmation_ms'])
            closed=copy.deepcopy(rr);closed[-1][4]=94.8 if short else 105.2
            ready=level_watch(closed,side,*chart.pivots(closed),chart.CONFIG)
            self.assertTrue(ready['model_ready'])

    def test_level_watch_does_not_recycle_old_breaks_or_broken_support(self):
        for mode in ('old','broken'):
            rr=test_chart_selection.SelectionTests().fixture()
            if mode=='old':rr[-2][2]=106;rr[-2][4]=105.1
            else:rr[-1][3]=99
            self.assertIsNone(level_watch(rr,'LONG',*chart.pivots(rr),chart.CONFIG))

    def test_no_shape_can_be_watch_only_with_executable_plan(self):
        f,price=sample();level=dict(model='CONFIRMED_LEVEL_WATCH',trigger=price+.05,
            stop=price-.25,model_anchor_ms=f['5m'][-3][0],model_ready=False,confirmation_ms=None,
            pattern_evidence={})
        with patch.object(chart,'setups',return_value=[]),patch.object(chart,'level_watch',return_value=level):
            old=chart.assess('X',f,price,50e6,NOW,comparison_v3=True)
            new=chart.assess('X',f,price,50e6,NOW)
        self.assertEqual(old['reason'],'NO_DEFINED_SETUP')
        self.assertEqual(new['stage'],'WATCH')
        self.assertGreaterEqual(new['geometry']['net_reward_risk'],1)
