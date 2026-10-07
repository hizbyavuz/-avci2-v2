#!/usr/bin/env python3
"""Keep polling through the entire requested live window, including 5m closes."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from long_short_live_pool import watch_window_end


class LiveWindowCoverageTests(unittest.TestCase):
    def test_boundary_does_not_cut_window_short(self):
        # Example: watch begins at 23:43:46; 5m close at 23:45:00.
        start=23*3600+43*60+46
        end=watch_window_end(start,330,True,4)
        self.assertEqual(end-start,330)
        self.assertGreater(end,23*3600+45*60+4)

    def test_watch_full_window_regardless_of_start_second(self):
        for start in (0,1,295,299,300,301,599,602,7199):
            with self.subTest(start=start):
                self.assertEqual(watch_window_end(start,330,True,4)-start,330)

    def test_zero_runtime_is_not_negative(self):
        self.assertEqual(watch_window_end(100,-20),100)


if __name__=="__main__":
    unittest.main()
