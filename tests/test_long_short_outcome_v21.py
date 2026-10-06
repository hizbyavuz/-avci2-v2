import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from long_short_outcome_tracker_v21 import _direction_return, _barrier_path


def bar(ts, o, h, l, c):
    # Binance-style 1m row subset.
    return [ts, str(o), str(h), str(l), str(c), "0", ts+59999]


class OutcomeV21Tests(unittest.TestCase):
    def test_long_direction_return(self):
        self.assertAlmostEqual(_direction_return("LONG",100,102),2.0,places=6)

    def test_short_direction_return(self):
        self.assertAlmostEqual(_direction_return("SHORT",100,98),(100/98-1)*100,places=6)

    def test_same_bar_stop_first_long(self):
        rows=[bar(0,100,102,98,101)]
        first,_,_,_,_=_barrier_path("LONG",100,99,101.5,103,rows)
        self.assertEqual(first,"STOP")

    def test_tp1_before_stop_long(self):
        rows=[
            bar(0,100,101.6,99.4,101.2),
            bar(60000,101.2,102,100.5,101.7),
        ]
        first,_,_,mfe,mae=_barrier_path("LONG",100,99,101.5,103,rows)
        self.assertEqual(first,"TP1")
        self.assertGreater(mfe,1.5)
        self.assertGreater(mae,-1.0)


if __name__=="__main__":
    unittest.main()
