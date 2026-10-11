"""451 observation tests: observation is never a trade signal."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from long_short_direction_engine import decide_direction

class TestFuturesMissingObservation(unittest.TestCase):
    def decide(self, **overrides):
        params = dict(long_score=65, short_score=15, deriv_ready=False,
                      actionable_liquidity_ok=True, external_only_unverified=False)
        params.update(overrides)
        return decide_direction(**params)

    def test_missing_futures_is_watch_only(self):
        r = self.decide()
        self.assertEqual(r['direction'], 'LONG')
        self.assertTrue(r['observation_only'])
        self.assertFalse(r['eligible'])
        self.assertIn('derivatives_incomplete', r['hard_blockers'])

    def test_missing_futures_short_is_watch_only(self):
        r = self.decide(long_score=10, short_score=70)
        self.assertEqual(r['direction'], 'SHORT')
        self.assertTrue(r['observation_only'])
        self.assertFalse(r['eligible'])

    def test_illiquid_never_observed(self):
        self.assertFalse(self.decide(actionable_liquidity_ok=False)['observation_only'])

    def test_unverified_never_observed(self):
        self.assertFalse(self.decide(external_only_unverified=True)['observation_only'])

    def test_directionless_never_observed(self):
        self.assertFalse(self.decide(long_score=20, short_score=15)['observation_only'])

    def test_complete_data_retains_trade_gate(self):
        r = self.decide(deriv_ready=True)
        self.assertTrue(r['eligible'])
        self.assertFalse(r['observation_only'])

if __name__ == '__main__':
    unittest.main()
