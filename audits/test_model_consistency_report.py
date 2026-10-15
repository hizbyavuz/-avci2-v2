import copy
import unittest
from model_consistency_report import feasibility,review


class ModelReviewTests(unittest.TestCase):
    def evidence(self):
        return dict(entry=100,stop=99,target=102,cost_pct=.3,min_net_r=1,required_room_pct=.9,version='v4',reason='OK')

    def sample(self):
        import json
        def row(prefix,p,stamp='2026-10-15T02:00:01Z'):
            return dict(timestamp=stamp,message=prefix+' '+json.dumps(p))
        contract=dict(version='audit',production_commit='commit',source_sha256='source',strategy_id='id',geometry_version='v4',required_reward_formula='formula',limitations=[])
        bundle={'scope':dict(commit='commit',source_sha256='source',strategy_id='id',cutoff_utc='2026-10-15T02:01:00Z'),
            'streams':{'identity':[row('LS_STRATEGY_EFFECTIVE',dict(id='id',source_sha256='source'))],
                'geometry':[row('STRUCTURAL_PLAN_V4',dict(symbol='X',direction='LONG',qualified=True,evidence=self.evidence()))],
                'direction':[row('PRODUCTION_DIRECTION_AUDIT',dict(symbol='X',direction='LONG',eligible=True,strategy_id='id'),stamp='2026-10-15T02:00:00.5Z')],
                'execution':[row('INDEPENDENT_PAPER_SUMMARY',dict(groups={}))]}}
        return bundle,contract

    def test_cost_and_risk_imply_required_reward(self):
        e=self.evidence();r=feasibility(e,'LONG')
        self.assertAlmostEqual(r['risk_pct'],1)
        self.assertAlmostEqual(r['required_reward_pct'],1.6)
        self.assertAlmostEqual(r['net_r'],1.7/1.3)
        self.assertTrue(r['passes_economic_gates'])
        e.update(stop=101,target=98)
        self.assertAlmostEqual(feasibility(e,'SHORT')['net_r'],r['net_r'])

    def test_target_below_cost_cannot_be_counted_profitable(self):
        e=self.evidence();e['target']=100.2
        r=feasibility(e,'LONG')
        self.assertTrue(r['target_not_above_cost'])
        self.assertLess(r['net_target_pct'],0)
        self.assertFalse(r['passes_economic_gates'])

    def test_invalid_levels_missing_and_nonfinite_are_not_zeroes(self):
        e=self.evidence();e['target']=99.5
        self.assertEqual(feasibility(e,'LONG'),{'state':'INVALID_GEOMETRY'})
        for bad in (None,float('nan'),-1):
            e=self.evidence();e['cost_pct']=bad
            self.assertEqual(feasibility(e,'LONG')['state'],'MISSING_OR_INVALID_INPUT')

    def test_repeated_scan_not_counted_twice(self):
        b,c=self.sample();b['streams']['geometry']*=3
        r=review(b,c)
        self.assertEqual(r['distinct_scan_records'],1)
        self.assertEqual(r['direction_and_geometry_passed'],1)
        self.assertEqual(r['excluded']['repeated_symbol_direction_5m_bucket'],2)

    def test_ambiguous_join_not_promoted_to_approved_direction(self):
        b,c=self.sample();b['streams']['direction']*=2
        r=review(b,c)
        self.assertEqual(r['direction_joined'],0)
        self.assertIsNone(r['cases'][0]['direction_eligible'])
        self.assertEqual(r['direction_and_geometry_passed'],0)

    def test_future_record_excluded_and_different_cohort_rejected(self):
        b,c=self.sample();b['streams']['geometry'][0]['timestamp']='2026-10-15T02:02:00Z'
        self.assertEqual(review(b,c)['distinct_scan_records'],0)
        b['scope']['strategy_id']='different'
        with self.assertRaises(ValueError):review(b,c)

    def test_interleaved_line_is_reported_and_empty_ledger_is_not_performance(self):
        b,c=self.sample();bad=copy.deepcopy(b['streams']['geometry'][0]);bad['message']='STRUCTURAL_PLAN_V4 OTHER_LOG'
        b['streams']['geometry'].append(bad)
        r=review(b,c)
        self.assertEqual(r['log_issues']['geometry']['unparseable_or_interleaved'],1)
        self.assertEqual(r['confirmation_delay']['status'],'NOT_ESTIMABLE_NO_TRIGGERED_SAMPLE')
        self.assertIsNone(r['confirmation_delay']['mean_seconds'])
        self.assertFalse(r['edge_proven'])


if __name__=='__main__':unittest.main()
