import copy
import unittest
from long_short_short_retest import assess,geometry,MODELS,HOUR as H

T=1785542400000


def fixture(model):
    base=95. if model==MODELS[0] else 104.
    rows=[dict(open_ms=T+i*H,close_ms=T+(i+1)*H-1,open=base,high=base+1,
               low=base-1,close=base,quote_volume=100000.,taker_buy_quote=40000.) for i in range(169)]
    def bar(i,o,h,l,c):rows[i].update(open=o,high=h,low=l,close=c)
    if model==MODELS[0]:
        bar(120,95,100,80,95)
        bar(164,95,101.2,94.8,101)
        bar(165,101,101.1,99,99.2)
        bar(166,99.3,100.1,98.8,99)
        bar(167,99.2,99.6,98.9,99)
        bar(168,99,99.1,98.5,98.6)
    else:
        bar(120,104,105,90,104)
        bar(130,104,110,103,104)
        bar(145,104,108,103,104)
        bar(151,104,105,100,104)
        bar(164,104,104.2,99.3,99.5)
        bar(165,99.5,100.1,99.3,99.6)
        bar(166,99.6,99.9,99.1,99.4)
        bar(167,99.4,99.5,99.2,99.3)
        bar(168,99.3,99.4,98.8,98.9)
    return rows,rows[-1]['close_ms']+1000


class ShortRetestTests(unittest.TestCase):
    def test_two_different_structures_produce_only_research_plans(self):
        for model in MODELS:
            rows,now=fixture(model);p=assess('TESTUSDT',rows,now,model)
            self.assertEqual(p['stage'],'READY',p)
            self.assertLess(p['break_ms'],p['retest_ms'])
            self.assertLess(p['retest_ms'],p['confirmation_ms'])
            self.assertGreater(p['stop'],p['price'])
            self.assertFalse(p['actionable']);self.assertIsNone(p['win_probability'])

    def test_drop_without_retest_is_not_a_signal(self):
        rows,now=fixture(MODELS[0])
        for i in (166,167):rows[i].update(open=98.7,high=98.8,low=98.6,close=98.7)
        rows[168].update(open=98.7,high=98.8,low=98.3,close=98.4)
        self.assertEqual(assess('TESTUSDT',rows,now,MODELS[0])['reason'],'NO_RETEST_SETUP')

    def test_reclaimed_level_invalidates_bear_case(self):
        rows,now=fixture(MODELS[0]);rows[167].update(open=99,high=100.4,low=98.9,close=100.2)
        self.assertEqual(assess('TESTUSDT',rows,now,MODELS[0])['stage'],'REJECTED')

    def test_bear_retest_requires_lower_highs_known_before_break(self):
        rows,now=fixture(MODELS[1]);rows[145]['high']=112.
        self.assertEqual(assess('TESTUSDT',rows,now,MODELS[1])['reason'],'NO_RETEST_SETUP')

    def test_future_candles_cannot_change_past_decision(self):
        for model in MODELS:
            rows,now=fixture(model);expected=assess('TESTUSDT',rows,now,model)
            future=copy.deepcopy(rows[-1]);future.update(open_ms=rows[-1]['open_ms']+H,close_ms=rows[-1]['close_ms']+H,high=float('nan'))
            self.assertEqual(expected,assess('TESTUSDT',rows+[future],now,model))

    def test_missing_flow_gap_and_stale_confirmation_reject(self):
        rows,now=fixture(MODELS[0]);self.assertEqual(assess('TESTUSDT',rows,now+H,MODELS[0])['reason'],'STALE_CONFIRMATION')
        rows[50]['taker_buy_quote']=None
        self.assertEqual(assess('TESTUSDT',rows,now,MODELS[0])['reason'],'VOLUME_OR_TAKER_MISSING')
        rows,now=fixture(MODELS[0]);rows[50]['open_ms']+=H
        self.assertEqual(assess('TESTUSDT',rows,now,MODELS[0])['stage'],'REJECTED')

    def test_cost_and_chase_checks_do_not_move_stop(self):
        rows,now=fixture(MODELS[0]);p=assess('TESTUSDT',rows,now,MODELS[0]);stop=p['stop']
        self.assertEqual(geometry(p,p['trigger']+.01),'CONFIRMATION_NOT_HELD')
        self.assertEqual(geometry(p,p['trigger']-1.01*p['atr_1h']),'LATE_CHASE')
        p['obstacle']=p['price']-2*(p['stop']-p['price'])
        self.assertEqual(geometry(p,p['price']),'INSUFFICIENT_NET_KNOWN_ROOM')
        self.assertEqual(p['stop'],stop)

    def test_bullish_flow_cannot_be_called_selling_pressure(self):
        rows,now=fixture(MODELS[1])
        for row in rows[-3:]:row['taker_buy_quote']=60000.
        self.assertEqual(assess('TESTUSDT',rows,now,MODELS[1])['reason'],'SELL_PRESSURE_NOT_CONFIRMED')


if __name__=='__main__':unittest.main()
