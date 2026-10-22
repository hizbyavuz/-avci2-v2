import unittest
from long_short_funding_bounds import measure,HOUR as H,MINUTE as M

T=1786752000000


class FundingBoundsTests(unittest.TestCase):
    def fixture(self):
        rates=[dict(time_ms=T+i*H,rate=.001,interval_hours=1.) for i in range(4)]
        marks={T+i*H:(99.,101.) for i in range(4)}
        exit_=dict(kind='STOP',at_ms=T+2*H,time_upper_ms=T+2*H+M-1)
        return rates,marks,exit_

    def test_paid_and_received_funding_sign_and_ambiguous_stop(self):
        rates,marks,exit_=self.fixture()
        long=measure('LONG',100.,T+M,exit_,1.,rates,marks,(T,T+4*H))
        self.assertAlmostEqual(long['funding_cost_low_pct'],.099)
        self.assertAlmostEqual(long['funding_cost_high_pct'],.202)
        short=measure('SHORT',100.,T+M,exit_,1.,rates,marks,(T,T+4*H))
        self.assertAlmostEqual(short['funding_cost_low_pct'],-.202)
        self.assertAlmostEqual(short['funding_cost_high_pct'],-.099)
        self.assertIsNone(long['exact_full_net_pct'])

    def test_gap_and_missing_mark_do_not_become_zero(self):
        rates,marks,exit_=self.fixture()
        with self.assertRaisesRegex(ValueError,'SCHEDULE_GAP'):
            measure('LONG',100.,T+M,exit_,1.,rates[:1]+rates[2:],marks,(T,T+4*H))
        del marks[T+H]
        with self.assertRaisesRegex(ValueError,'MARK_MINUTE_MISSING'):
            measure('LONG',100.,T+M,exit_,1.,rates,marks,(T,T+4*H))

    def test_negative_funding_can_turn_short_profit_into_loss(self):
        rates,marks,exit_=self.fixture()
        for r in rates:r['rate']=-.01
        out=measure('SHORT',100.,T+M,exit_,.5,rates,marks,(T,T+4*H))
        self.assertEqual(out['sign_under_cost_bounds'],'NEGATIVE')
        self.assertLess(out['fee_slippage_and_funding_net_high_pct'],0)

    def test_no_settlement_during_known_covered_short_hold_is_zero(self):
        rates,marks,exit_=self.fixture();exit_.update(at_ms=T+2*M,time_upper_ms=T+3*M-1)
        out=measure('LONG',100.,T+M,exit_,.5,rates,{},(T,T+4*H))
        self.assertEqual(out['funding_cost_high_pct'],0.)
        with self.assertRaisesRegex(ValueError,'COVERAGE'):
            measure('LONG',100.,T+M,exit_,.5,rates,{},None)

    def test_simultaneous_entry_settlement_does_not_assume_order(self):
        rates,marks,exit_=self.fixture();exit_.update(at_ms=T+M,time_upper_ms=T+2*M-1)
        out=measure('LONG',100.,T,exit_,.5,rates,marks,(T,T+4*H))
        self.assertEqual(out['funding_cost_low_pct'],0.)
        self.assertAlmostEqual(out['funding_cost_high_pct'],.101)
        self.assertTrue(out['events'][0]['entry_settlement_simultaneous'])


if __name__=='__main__':unittest.main()
