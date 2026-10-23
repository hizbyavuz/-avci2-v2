import unittest
from structure_engine.annotations import ema,rsi,analyze
from structure_engine.chart import drawing_spec
from structure_engine.core import assess
from tests.test_structure_context import waves,fixture

class AnnotationTests(unittest.TestCase):
    def test_indicator_arithmetic_and_wilder_boundaries(self):
        self.assertEqual(ema([1,2,3],3),[1,1.5,2.25])
        self.assertEqual(rsi([10]*20)[-1],50)
        self.assertEqual(rsi(list(range(1,21)))[-1],100)
        self.assertEqual(rsi(list(range(21,1,-1)))[-1],0)
        self.assertIsNone(rsi([10]*14)[-1])
    def test_future_candles_do_not_change_past_indicator_or_event(self):
        rows=waves()
        short=analyze(rows[:-5]);full=analyze(rows)
        self.assertEqual(short['indicators']['rsi14'],full['indicators']['rsi14'][:-5])
        self.assertEqual(short['indicators']['macd'],full['indicators']['macd'][:-5])
        cutoff=rows[-6]['close_ms']
        comparable=[e for e in short['events'] if e['at_ms']>=rows[-60]['open_ms']]
        self.assertEqual(comparable,[e for e in full['events'] if e['known_ms']<=cutoff])
    def test_all_events_and_zones_are_closed_and_directionally_valid(self):
        rows=waves();a=analyze(rows)
        self.assertTrue(all(z['low']<z['high'] and z['known_ms']<=rows[-1]['close_ms'] for z in a['zones']))
        self.assertTrue(all(e['anchor_ms']<e['at_ms'] and e['known_ms']<=rows[-1]['close_ms'] for e in a['events']))
        self.assertEqual(a['decision_effect'],'NONE')
    def test_touched_fvg_not_reported_as_active(self):
        rows=waves(40)
        a,b,c=rows[-3:]
        a.update(open=100,close=100,high=101,low=99)
        b.update(open=102,close=103,high=104,low=101)
        c.update(open=104,close=104,high=105,low=103)
        result=analyze(rows)
        self.assertTrue(any(z['kind']=='FVG' and z['known_ms']==c['close_ms'] for z in result['zones']))
        touched=dict(c,open_ms=c['open_ms']+900000,close_ms=c['close_ms']+900000,open=104,close=102,high=105,low=100)
        result=analyze(rows+[touched])
        self.assertFalse(any(z['kind']=='FVG' and z['known_ms']==c['close_ms'] for z in result['zones']))

if __name__=='__main__':unittest.main()
