import unittest
from long_short_short_retest import assess,MODELS,MINUTE
from research.short_v2.replay_short import audit_one,summarize
from tests.test_short_retest import fixture


class ShortReplayTests(unittest.TestCase):
    def test_missing_entry_kept_in_denominator(self):
        rows,now=fixture(MODELS[0]);p=assess('TESTUSDT',rows,now,MODELS[0])
        item=audit_one(p,[],[])
        self.assertEqual(item['status'],'ENTRY_DATA_MISSING')
        report=summarize([dict(plan=p,audit=item)])[MODELS[0]]
        self.assertEqual(report['candidates'],1)
        self.assertEqual(report['unknown_or_open_plan_count'],1)
        self.assertIsNone(report['complete_path_conditional']['mean_fee_only_r'])

    def test_closed_before_later_gap_is_separate_from_complete_cohort(self):
        rows,now=fixture(MODELS[0]);p=assess('TESTUSDT',rows,now,MODELS[0])
        t=(now//MINUTE+1)*MINUTE;entry=p['price']
        path=[dict(open_ms=t,close_ms=t+MINUTE-1,open=entry,high=p['stop']+.1,
                   low=entry-.1,close=entry)]
        item=audit_one(p,path,[t])
        self.assertEqual(item['status'],'MEASURED')
        self.assertTrue(item['outcome']['plan_closed'])
        self.assertEqual(item['outcome']['status'],'DATA_GAP')
        self.assertLess(item['fee_slippage_only_r'],-1)
        report=summarize([dict(plan=p,audit=item)])[MODELS[0]]
        self.assertEqual(report['complete_path_conditional']['count'],0)
        self.assertEqual(report['closed_incomplete_path_conditional']['count'],1)
        self.assertEqual(report['unknown_or_open_plan_count'],0)


if __name__=='__main__':unittest.main()
