import unittest
from long_short_chart_selection_audit import movement

SOURCE='BINANCE_UM_FUTURES_WEBSOCKET'


class MovementAuditTests(unittest.TestCase):
    def test_partial_signal_minute_does_not_manufacture_move(self):
        rows=[(0,59999,500,1,100,SOURCE)]
        rows += [(t,t+59999,102,99,101,SOURCE) for t in range(60000,960000,60000)]
        m=movement(100,30000,rows)
        self.assertEqual(m['state'],'MEASURED')
        self.assertAlmostEqual(m['up_excursion_pct'],2)
        self.assertAlmostEqual(m['down_excursion_pct'],1)
        self.assertAlmostEqual(m['return_pct'],1)
        self.assertGreater(m['effective_minutes'],15)

    def test_missing_or_unverified_bar_remains_unresolved(self):
        rows=[(t,t+59999,102,99,101,SOURCE) for t in range(60000,960000,60000)]
        self.assertEqual(movement(100,30000,rows[1:])['state'],'UNRESOLVED_MISSING_PATH')
        rows[-1]=(*rows[-1][:-1],'BINANCE_SPOT')
        self.assertEqual(movement(100,30000,rows)['state'],'UNRESOLVED_INVALID_PATH')
