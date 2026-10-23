import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'research'/'swing_v1'))
import baseline
from long_short_swing import HOUR


def bars(n, offset=0):
    return [dict(open_ms=(i+offset)*HOUR,close_ms=(i+offset+1)*HOUR-1,
        open=100+i/100,high=101+i/100,low=99+i/100,close=100+i/100,
        quote_volume=100_000,taker_buy_quote=50_000) for i in range(n)]


class BaselineTests(unittest.TestCase):
    def test_point_in_time_panel_is_unchanged_by_later_prices_and_missing_future(self):
        rows=bars(190)
        cut=rows[168]['close_ms']
        original=baseline.panel({'AUSDT':rows[:169]})[cut]
        changed=copy.deepcopy(rows)
        for b in changed[169:]:
            for field in ('open','high','low','close'):b[field]*=1000
        self.assertEqual(baseline.panel({'AUSDT':changed})[cut],original)
        self.assertAlmostEqual(original[0]['return_24h_pct'],100*(rows[168]['close']/rows[144]['close']-1))
        self.assertEqual(original[0]['quote_volume_24h'],2_400_000)

    def test_gap_expires_only_after_a_full_contiguous_window(self):
        rows=bars(180)+bars(170,offset=181)
        found=baseline.panel({'AUSDT':rows})
        self.assertNotIn(rows[347]['close_ms'],found)
        self.assertIn(rows[348]['close_ms'],found)
        bad=bars(169);bad[10]['taker_buy_quote']=None
        self.assertEqual(baseline.panel({'AUSDT':bad}),{})

    def test_ranking_is_directional_tied_by_symbol_and_never_refills_missing_future(self):
        candidates=[dict(symbol=s,i=168,return_24h_pct=v,quote_volume_24h=3e6)
            for s,v in [('B',5),('A',5),('C',4),('D',3),('E',2),('F',1)]]
        ranked=baseline.rank_at(candidates,'LONG')
        self.assertEqual([r['symbol'] for r in ranked],['A','B','C','D','E'])
        self.assertEqual([r['symbol'] for r in baseline.rank_at(candidates,'SHORT')],['F','E','D','C','A'])
        data={s:bars(337 if s!='A' else 169) for s in 'ABCDEF'}
        selections=[baseline.selection(data,c,'LONG') for c in ranked]
        self.assertEqual(selections[0]['raw']['status'],'IMMATURE_OR_GAP')
        self.assertNotIn('F',[r['symbol'] for r in selections])

    def test_incomplete_top_five_excludes_hour_without_replacing_winner(self):
        data={s:bars(337 if s!='A' else 169) for s in 'ABCDEF'}
        stamp=data['B'][168]['close_ms']
        candidates=[dict(symbol=s,i=168,return_24h_pct=6-i,quote_volume_24h=3e6)
                    for i,s in enumerate('ABCDEF')]
        raw=baseline.replay.raw_forward(data['B'],168,'LONG')
        screen={'records':[{'plan':{'direction':'LONG','confirmation_ms':stamp,'symbol':'B',
                    'move_before_signal_pct':2},'raw':raw,'control':{'raw':raw}}]}
        with patch.object(baseline,'panel',return_value={stamp:candidates}):
            result=baseline.compare(data,screen)
        self.assertEqual(result['summary']['LONG']['equal_hour']['paired_hours'],0)
        self.assertEqual(result['summary']['LONG']['exclusion_reasons'],{'BASELINE_INCOMPLETE':1})

    def test_hour_weights_and_block_resampling_keep_shared_hours_together(self):
        keys=['raw_reach_30_pct','mean_raw_mfe_pct','mean_raw_mae_pct',
              'mean_directional_close_168h_pct','mean_move_before_signal_pct']
        groups=[]
        for d in range(10):
            groups.append(dict(confirmation_ms=(d+1)*24*HOUR-1,
                swing={k:20 for k in keys},momentum={k:10 for k in keys},
                control={**{k:5 for k in keys},'mean_move_before_signal_pct':None}))
        stats=baseline.compare_hours(groups)
        self.assertEqual(stats['swing_minus_momentum_reach_pp'],10)
        self.assertIsNone(stats['control']['mean_move_before_signal_pct'])
        self.assertEqual(baseline.block_interval(groups,repeats=20)['reach_difference_pp_interval_90'],[10,10])


if __name__=='__main__':unittest.main()
