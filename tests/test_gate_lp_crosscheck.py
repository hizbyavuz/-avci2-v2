import unittest

from gate_lp_crosscheck import parse_summary


class LpCrosscheckTest(unittest.TestCase):
    def test_token_summary_never_claims_pool_verification(self):
        result = parse_summary("mint", {"mint": "mint", "lpLockedPct": 80})
        self.assertEqual(result["status"], "OBSERVED")
        self.assertIs(result["pool_match_verified"], False)

    def test_wrong_mint_and_missing_lp_stay_unknown(self):
        self.assertEqual(parse_summary("one", {"mint": "two",
                                                "lpLockedPct": 80})["status"],
                         "UNKNOWN")
        self.assertEqual(parse_summary("one", {"mint": "one"})["status"],
                         "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
