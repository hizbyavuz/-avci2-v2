import copy
import unittest
from unittest.mock import patch
import long_short_chart_first as chart
from long_short_chart_setups import swing_breakout, setups
from test_chart_first import sample, NOW


class SelectionTests(unittest.TestCase):
    def test_invalid_first_pattern_does_not_hide_valid_second_both_sides(self):
        for short in (False,True):
            frames,price=sample(short)
            h,l=chart.pivots(frames['5m']);side='SHORT' if short else 'LONG'
            valid=setups(frames['5m'],side,h,l,chart.CONFIG)[0]
            bad=dict(valid,stop=price+(1 if not short else -1),model='INVALID_FIRST')
            with patch.object(chart,'setups',return_value=[bad,valid]):
                result=chart.assess('X',frames,price,50e6,NOW)
            self.assertEqual(result['stage'],'READY')
            self.assertEqual(result['pattern_evaluations'][0]['reason'],'LOCAL_IDEA_INVALIDATED')
            self.assertEqual(result['pattern_evaluations'][1]['reason'],'OK')

    def test_ready_pattern_has_priority_over_valid_watch(self):
        f,p=sample();h,l=chart.pivots(f['5m'])
        ready=setups(f['5m'],'LONG',h,l,chart.CONFIG)[0]
        watch=dict(ready,model='WATCH_FIRST',model_ready=False,confirmation_ms=None)
        with patch.object(chart,'setups',return_value=[watch,ready]):
            result=chart.assess('X',f,p,50e6,NOW)
        self.assertEqual(result['stage'],'READY')
        self.assertEqual(result['model'],ready['model'])

    def fixture(self):
        # Real confirmed swings: high 105, subsequent higher low 100 vs 98.
        values=[(99,100,98.5,99)]*24+[
            (99,100,98,99),(99,101,98.5,100),(100,103,99,102),
            (102,105,101,104),(104,104.5,102,103),(103,104,101,102),
            (102,103,100,101),(101,103.5,100.5,102),(102,104,101,103),
            (103,105.5,102.5,105.2)]
        return [[i*300000,*v,100,(i+1)*300000-1] for i,v in enumerate(values)]

    def test_confirmed_swing_breakout_and_mirror(self):
        rows=self.fixture()
        for short in (False,True):
            rr=rows if not short else [[x[0],200-x[1],200-x[3],200-x[2],200-x[4],*x[5:]] for x in rows]
            h,l=chart.pivots(rr)
            p=swing_breakout(rr,'SHORT' if short else 'LONG',h,l,chart.CONFIG)
            self.assertIsNotNone(p)
            self.assertTrue(p['model_ready'])
            self.assertEqual(p['trigger'],95 if short else 105)
            self.assertEqual(p['confirmation_ms'],rr[-1][6])

    def test_no_repeated_break_or_broken_higher_low(self):
        rows=self.fixture()
        for mode in ('old_break','stop_broken','lower_low'):
            rr=copy.deepcopy(rows)
            if mode=='old_break':rr[-2][2]=106;rr[-2][4]=105.1
            if mode=='stop_broken':rr[-1][3]=99
            if mode=='lower_low':rr[-4][3]=97
            h,l=chart.pivots(rr)
            self.assertIsNone(swing_breakout(rr,'LONG',h,l,chart.CONFIG),mode)

    def test_no_pattern_bypasses_cost_or_chase_guards(self):
        f,p=sample();h,l=chart.pivots(f['5m'])
        candidate=dict(setups(f['5m'],'LONG',h,l,chart.CONFIG)[0],model='SWING_BREAKOUT')
        with patch.object(chart,'setups',return_value=[candidate]):
            with patch.object(chart,'first_obstacle',return_value=(p+.01,'5m')):
                self.assertEqual(chart.assess('X',f,p,50e6,NOW)['reason'],'OBSTACLE_WITHIN_COST_ALLOWANCE')
            self.assertEqual(chart.assess('X',f,p+5,50e6,NOW)['reason'],'ENTRY_ALREADY_EXTENDED')
