import copy
import math
import unittest

import long_short_trader_context as c

H=c.HOUR;M=c.MINUTE;T=1786752000000


def context():
    rows=[dict(time_ms=T-25*H+i*5*M,available_ms=T-25*H+(i+1)*5*M,
               oi_base=1000.+i,account_ratio=1.2) for i in range(301)]
    funding=[dict(time_ms=T-4*H,available_ms=T-4*H+M,rate=.0001,interval_hours=4.)]
    return rows,funding


class ContextTests(unittest.TestCase):
    def test_archive_sort_and_future_prefix_invariance(self):
        m,f=context();base=c.derivative_features(m,f,T)
        self.assertGreater(base['oi_change_4h_pct'],0)
        self.assertEqual(base,c.derivative_features(list(reversed(m)),f,T))
        future=dict(time_ms=T+M,available_ms=T+M,oi_base=float('nan'),account_ratio=99)
        self.assertEqual(base,c.derivative_features(m+[future],f,T))

    def test_gap_is_not_neutral_and_not_forward_filled(self):
        m,f=context();del m[-8]
        d=c.derivative_features(m,f,T)
        self.assertIsNone(d['oi_change_1h_pct']);self.assertEqual(d['errors']['oi_1h'],'OI_WINDOW_INCOMPLETE')

    def test_duplicate_conflict_and_identical_duplicate(self):
        m,f=context();original=c.derivative_features(m,f,T)
        self.assertEqual(original,c.derivative_features(m+[copy.deepcopy(m[-2])],f,T))
        bad=dict(m[-2],oi_base=99.)
        self.assertEqual(c.derivative_features(m+[bad],f,T)['errors']['metrics'],'CONFLICTING_DUPLICATE')

    def test_no_future_funding_or_fixed_eight_hour_interval(self):
        m,f=context();d=c.derivative_features(m,f,T)
        p=dict(direction='LONG',stage='READY',risk_pct=4.)
        price=dict(return_1h_pct=1.,closed_4h_return_pct=1.,btc_residual_4h_log_pct=1.,flow_4h=.1)
        r=c.evaluate(p,price,d)
        self.assertAlmostEqual(r['paid_carry_72h_scenario_pct'],.18)
        self.assertTrue(r['variants']['CONTEXT']['pass_candidate'])
        f.append(dict(time_ms=T+1,available_ms=T+1,rate=.2,interval_hours=1.))
        self.assertEqual(d,c.derivative_features(m,f,T))

    def test_oi_is_not_a_direction_vote_and_unwind_waits(self):
        m,f=context();d=c.derivative_features(m,f,T)
        price=dict(return_1h_pct=-1.,closed_4h_return_pct=-1.,btc_residual_4h_log_pct=-1.,flow_4h=-.1)
        short=c.evaluate(dict(direction='SHORT',stage='READY',risk_pct=4.),price,d)
        self.assertTrue(short['variants']['CONTEXT']['pass_candidate'])
        long=c.evaluate(dict(direction='LONG',stage='READY',risk_pct=4.),price,d)
        self.assertFalse(long['variants']['STRUCTURE']['pass_candidate'])
        d['oi_change_1h_pct']=-1.
        self.assertFalse(c.evaluate(dict(direction='SHORT',stage='READY',risk_pct=4.),price,d)['variants']['PARTICIPATION']['pass_candidate'])

    def test_missing_and_nonfinite_never_pass(self):
        p=dict(direction='LONG',stage='READY',risk_pct=4.)
        r=c.evaluate(p,{},{});self.assertFalse(r['variants']['CONTEXT']['pass_candidate'])
        self.assertFalse(r['actionable']);self.assertIsNone(r['win_probability'])
        p['direction']='NONE'
        with self.assertRaises(ValueError):c.evaluate(p,{}, {})

    def test_crowding_combines_paid_rate_not_contrarian_reversal(self):
        m,f=context();d=c.derivative_features(m,f,T);d['account_ratio']=3.
        price=dict(return_1h_pct=1.,closed_4h_return_pct=1.,btc_residual_4h_log_pct=1.,flow_4h=.1)
        p=dict(direction='LONG',stage='READY',risk_pct=4.)
        r=c.evaluate(p,price,d)
        self.assertIn('PAID_FUNDING_AND_CROWDED_SIDE',r['variants']['CONTEXT']['reasons'])
        self.assertEqual(r['direction'],'LONG')
        d['funding_rate']=-.001;r=c.evaluate(p,price,d)
        self.assertEqual(r['paid_carry_72h_scenario_pct'],0.)

    def test_beta_training_excludes_signal_window_and_daily_bar_is_closed(self):
        rows=[];btc=[]
        for i in range(169):
            t=T-169*H+i*H;b=100+2*math.sin(i/4)+i*.02
            for seq,price in ((rows,b**1.2),(btc,b)):
                seq.append(dict(open_ms=t,close_ms=t+H-1,open=price,high=price*1.01,
                    low=price*.99,close=price,quote_volume=100.,taker_buy_quote=55.))
        first=c.price_features(rows,btc,T)
        self.assertAlmostEqual(first['beta'],1.2)
        later=copy.deepcopy(rows)
        for b in later[-4:]:b['close']*=1.1;b['high']=b['close']*1.01
        after=c.price_features(later,btc,T)
        self.assertAlmostEqual(first['beta'],after['beta'])
        self.assertGreater(after['btc_residual_4h_log_pct'],first['btc_residual_4h_log_pct'])
        self.assertLess(first['closed_day_ms'],T)
        self.assertEqual(first['beta_training_end_ms'],rows[-5]['close_ms'])


if __name__=='__main__':unittest.main()
