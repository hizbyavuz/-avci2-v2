import unittest
from long_short_trade_plan import plan_at_book


class TradePlanTests(unittest.TestCase):
    def setup(self,side='LONG'):
        p=dict(direction=side,stage='READY',symbol='TESTUSDT',entry_valid_until_ms=10000,
               stop=97. if side=='LONG' else 103.,trigger=99. if side=='LONG' else 101.,atr_1h=3.,obstacle=None)
        b=dict(source='BINANCE_UM_FUTURES_REST',symbol='TESTUSDT',event_ms=1000,received_ms=1100,
               bids=[[99.9,100.],[99.8,100.]],asks=[[100.1,100.],[100.2,100.]])
        policy=dict(notional_usdt=1000.,round_trip_fee_bps=10.,exit_slippage_reserve_bps=10.,
                    max_spread_bps=30.,min_net_rr=1.5,paid_carry_scenario_pct=.1)
        return p,b,policy

    def test_long_short_book_side_target_and_entry_impact(self):
        for side,entry in [('LONG',100.1),('SHORT',99.9)]:
            p,b,k=self.setup(side);r=plan_at_book(p,b,1200,**k)
            self.assertEqual(r['status'],'GEOMETRY_READY',r)
            self.assertAlmostEqual(r['entry_vwap'],entry)
            self.assertGreater(r['entry_impact_from_mid_bps'],0)
            self.assertAlmostEqual(r['assumed_cost_pct'],.3)
            self.assertFalse(r['actionable']);self.assertIsNone(r['win_probability'])

    def test_insufficient_depth_and_stale_book_block(self):
        p,b,k=self.setup();k['notional_usdt']=100000.
        self.assertEqual(plan_at_book(p,b,1200,**k)['reason'],'INSUFFICIENT_VISIBLE_DEPTH')
        self.assertEqual(plan_at_book(p,b,9000,**k)['reason'],'BOOK_STALE_OR_FUTURE')

    def test_cost_can_reject_identical_chart_and_stop_is_not_moved(self):
        p,b,k=self.setup();before=plan_at_book(p,b,1200,**k)
        k['paid_carry_scenario_pct']=5.;after=plan_at_book(p,b,1200,**k)
        self.assertEqual(after['reason'],'NET_REWARD_RISK_BELOW_POLICY')
        self.assertEqual(before['stop'],after['stop'])

    def test_unknown_cost_not_zero_and_known_obstacle_is_binding(self):
        p,b,k=self.setup();k['paid_carry_scenario_pct']=None
        self.assertEqual(plan_at_book(p,b,1200,**k)['reason'],'INVALID_OR_MISSING_COST_POLICY')
        k['paid_carry_scenario_pct']=.1;p['obstacle']=101.
        self.assertEqual(plan_at_book(p,b,1200,**k)['reason'],'INSUFFICIENT_KNOWN_ROOM')


if __name__=='__main__':unittest.main()
