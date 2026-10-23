import unittest

from long_short_swing import HOUR, MINUTE
from research.exit_control_v1.compare import choose_peer, fixed_two_r, point_features, summary, block_interval


def bar(t, o=100., h=101., l=99., c=100.):
    return dict(open_ms=t,close_ms=t+MINUTE-1,open=o,high=h,low=l,close=c)


class ExitTests(unittest.TestCase):
    def test_stop_first_for_both_directions(self):
        for direction,stop,target in [('LONG',95.,110.),('SHORT',105.,90.)]:
            p=dict(direction=direction,stop=stop,atr_1h=1.)
            b=bar(0,100.,max(stop,target)+1,min(stop,target)-1,100.)
            out=fixed_two_r(p,0,100.,[b])
            self.assertEqual(out['exit']['kind'],'STOP')
            self.assertAlmostEqual(out['fee_r'],-1.06)

    def test_stop_gap_worsens_fill_and_favorable_gap_does_not_improve_target(self):
        p=dict(direction='SHORT',stop=105.,atr_1h=1.)
        loss=fixed_two_r(p,0,100.,[bar(0,110.,112.,109.,111.)])
        self.assertEqual(loss['exit']['price'],110.)
        self.assertAlmostEqual(loss['fee_r'],-2.06)
        win=fixed_two_r(p,0,100.,[bar(0,85.,86.,84.,85.)])
        self.assertEqual(win['exit']['price'],90.)
        self.assertAlmostEqual(win['fee_r'],1.94)

    def test_missing_path_is_not_a_loss_or_complete_and_closed_exit_is_retained(self):
        p=dict(direction='LONG',stop=95.,atr_1h=1.)
        out=fixed_two_r(p,0,100.,[bar(0),bar(2*MINUTE)])
        self.assertEqual(out['status'],'DATA_GAP');self.assertIsNone(out['fee_r'])
        self.assertEqual(out['missing_ms'],MINUTE)
        out=fixed_two_r(p,0,100.,[bar(0,100.,111.,99.,110.),bar(2*MINUTE)])
        self.assertEqual(out['status'],'DATA_GAP');self.assertAlmostEqual(out['fee_r'],1.94)
        self.assertIsNone(out['full_net_pct'])

    def test_timeout_uses_last_close_with_no_barrier(self):
        p=dict(direction='LONG',stop=95.,atr_1h=1.)
        path=[bar(i*MINUTE,c=101.) for i in range(168*60)]
        out=fixed_two_r(p,0,100.,path)
        self.assertEqual(out['status'],'COMPLETE');self.assertEqual(out['exit']['kind'],'TIMEOUT')
        self.assertAlmostEqual(out['fee_r'],.14)


class PeerTests(unittest.TestCase):
    def test_sparse_signal_days_are_not_inflated_by_empty_calendar_days(self):
        rows=[dict(confirmation_ms=d*24*HOUR-1,delta=float(d)) for d in (1,5,12)]
        interval=block_interval(rows,'delta')
        self.assertEqual(interval['signal_days'],3)
        self.assertEqual(interval['calendar_days'],12)

    def test_peer_selection_does_not_consume_future_or_minute_availability(self):
        p=dict(symbol='A',model='M',confirmation_ms=1,quote_volume_24h=3e6,atr_1h=1.,price=100.)
        peers=[dict(p,symbol=s) for s in 'ABCDEFG']
        picked=choose_peer(p,peers);self.assertNotEqual(picked['symbol'],'A')
        altered=[dict(x,future_return=999 if x['symbol']==picked['symbol'] else -999,minute_available=False) for x in peers]
        self.assertEqual(choose_peer(p,list(reversed(altered)))['symbol'],picked['symbol'])
        self.assertEqual(picked['nearest_count'],5)
        self.assertIsNone(choose_peer(p,[p]))

    def test_point_in_time_panel_ignores_invalid_future_and_rejects_history_gap(self):
        rows=[dict(open_ms=i*HOUR,close_ms=(i+1)*HOUR-1,open=100.,high=101.,low=99.,close=100.,
                   quote_volume=100000.,taker_buy_quote=50000.) for i in range(170)]
        expected=point_features('A',rows[:169])
        rows[-1]['close']=float('nan')
        self.assertEqual(point_features('A',rows),expected)
        self.assertEqual(len(expected),1)
        self.assertEqual(point_features('A',rows[:100]+rows[101:]),[])

    def test_missing_selected_peer_stays_unpaired_and_same_signal_exit_cohort_stays(self):
        exits={name:dict(status='COMPLETE',fee_r=v) for name,v in [('FROZEN_TRAIL',-1.),('FIXED_2R',2.)]}
        signal=dict(status='MEASURED',exits=exits)
        p=dict(symbol='A',confirmation_ms=HOUR-1)
        rows=[dict(cohort='M',plan=p,peer=dict(symbol='B'),signal=signal,control=dict(status='ENTRY_DATA_MISSING'))]
        result=summary(rows)['M']
        self.assertEqual(result['candidates'],1);self.assertEqual(result['paired_full_paths'],0)
        self.assertEqual(result['all_full_signals']['FROZEN_TRAIL']['count'],1)
        self.assertEqual(result['same_signals_fixed_minus_trail']['mean_fee_only_r'],3.)


if __name__=='__main__':unittest.main()
