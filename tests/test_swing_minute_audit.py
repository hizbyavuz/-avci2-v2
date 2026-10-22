from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'research'/'swing_v1'))
import minute_audit as audit
from long_short_swing import MINUTE as M, HOUR as H

START=int(datetime(2026,8,10,tzinfo=timezone.utc).timestamp()*1000)


def plan():
    return dict(symbol='TESTUSDT',direction='LONG',confirmation_ms=START-1001,
        observed_ms=START-1,price=100.,stop=97.,trigger=99.,atr_1h=2.,origin_price=99.,obstacle=None)


def bar(t,op=100.,hi=101.,lo=99.,cl=100.):
    return dict(open_ms=t,close_ms=t+M-1,open=op,high=hi,low=lo,close=cl,
        quote_volume=1e5,taker_buy_quote=5e4)


class MinuteAuditTests(unittest.TestCase):
    def test_missing_first_minute_is_never_replaced_and_stays_in_denominator(self):
        missing=audit.audit_one(plan(),[bar(START+M)])
        self.assertEqual(missing['status'],'ENTRY_DATA_MISSING')
        self.assertIsNone(missing['entry_price'])
        self.assertIsNone(missing['full_net_pct'])
        s=audit.summarize([missing])['ALL']
        self.assertEqual(s['all_candidates'],1)
        self.assertEqual(s['complete_path_conditional_technical_sample']['count'],0)
        self.assertIsNone(s['overall_strategy_win_rate'])

    def test_invalid_entry_is_abstention_not_loss_and_duplicate_data_is_invalid(self):
        invalid=audit.audit_one(plan(),[bar(START,103,104,102,103)])
        self.assertEqual(invalid['status'],'ENTRY_INVALID')
        self.assertEqual(invalid['reason'],'LATE_CHASE')
        self.assertEqual(audit.summarize([invalid])['ALL']['complete_path_conditional_technical_sample']['losing_fee_only'],0)
        duplicate=audit.audit_one(plan(),[bar(START),bar(START)])
        self.assertEqual(duplicate['status'],'DATA_INVALID')

    def test_stop_then_rally_and_costs_never_become_a_winner(self):
        rows=[bar(START,100,101,96,98),bar(START+M,98,140,97,130)]
        rows.extend(bar(START+i*M,130,131,129,130) for i in range(2,168*60))
        r=audit.audit_one(plan(),rows)
        self.assertEqual(r['outcome']['status'],'COMPLETE')
        self.assertEqual(r['reach_classification']['30'],'ONLY_AFTER_EXIT')
        self.assertAlmostEqual(r['cost_sensitivity']['0.3']['fee_slippage_only_pct'],-3.3)
        self.assertAlmostEqual(r['cost_sensitivity']['1.0']['fee_slippage_only_pct'],-4.)
        self.assertAlmostEqual(r['fee_slippage_only_r'],-1.1)
        self.assertEqual(audit.summarize([r])['ALL']['complete_path_conditional_technical_sample']['losing_fee_only'],1)

    def test_missing_after_exit_preserves_result_but_is_not_complete_path(self):
        r=audit.audit_one(plan(),[bar(START,100,101,96,98)])
        self.assertEqual(r['outcome']['status'],'DATA_GAP')
        s=audit.summarize([r])['ALL']
        self.assertEqual(s['complete_path_conditional_technical_sample']['count'],0)
        self.assertEqual(s['closed_but_raw_path_incomplete']['count'],1)
        self.assertAlmostEqual(s['closed_but_raw_path_incomplete']['mean_fee_only_pct'],-3.3)
        ambiguous=audit.audit_one(plan(),[bar(START,100,140,96,98)])
        self.assertEqual(ambiguous['reach_classification']['30'],'AMBIGUOUS_EXIT_MINUTE')

    def test_month_boundary_requests_next_month_and_merges_only_overlaps(self):
        a=int(datetime(2026,8,31,23,59,tzinfo=timezone.utc).timestamp()*1000)
        self.assertEqual(audit.months_between(a,a+M),['2026-08'])
        self.assertEqual(audit.months_between(a,a+2*M),['2026-08','2026-09'])
        self.assertEqual(audit.merge_windows([(1,3),(2,5),(5,7),(9,10)]),[[1,7],[9,10]])

    def test_prior_archive_hash_mismatch_stops_audit(self):
        with patch.object(audit.replay,'archive',return_value=({'status':'OK','sha256':'changed'},[])):
            with self.assertRaisesRegex(ValueError,'ARCHIVE_CHANGED'):
                audit.run([{'plan':plan()}],Path('/unused'),{('TESTUSDT','2026-08'):'original'})


if __name__=='__main__':unittest.main()
