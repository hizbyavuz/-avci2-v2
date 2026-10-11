import unittest
from gate_trader.early_outcomes import evaluate_path

class EarlyTest(unittest.TestCase):
    def test_long(self):
        bars=[(300,101,99,100),(600,104,99,103),(900,102,99,101)]
        result=evaluate_path('LONG',100,bars,300,900)
        self.assertEqual(result[5],1)
        self.assertEqual(result[6],0)
    def test_short(self):
        bars=[(300,101,95,96),(600,100,94,95),(900,99,93,94)]
        result=evaluate_path('SHORT',100,bars,300,900)
        self.assertAlmostEqual(result[2],6)
        self.assertEqual(result[6],1)
    def test_incomplete(self):
        self.assertIsNone(evaluate_path('LONG',100,[(300,101,99,100)],300,900))
