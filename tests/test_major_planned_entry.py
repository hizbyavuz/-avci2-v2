"""Regression cases for WATCH plans impossible at their required entry."""
import unittest
from unittest.mock import patch

import long_short_major_move as core
import test_major_move as fixtures


class PlannedEntry(unittest.TestCase):
    def test_recorded_sportfun_watch_cannot_enter_with_unchanged_levels(self):
        g=core.planned_geometry('SHORT',.01543,.01423,.016435107142857144,[])
        self.assertAlmostEqual(g['planned_entry'],.01423)
        self.assertAlmostEqual(g['planned_risk_pct'],15.49618512,places=7)
        self.assertLess((.016435107142857144/.01543-1)*100,8)
        self.assertGreater(g['planned_risk_pct'],8)

    def test_long_watch_accounts_for_higher_required_entry(self):
        g=core.planned_geometry('LONG',100,110,96,[(1,125)])
        self.assertEqual(g['planned_entry'],110)
        self.assertAlmostEqual(g['planned_risk_pct'],100*14/110)
        self.assertAlmostEqual(g['planned_room_pct'],100*15/110)

    def test_entry_already_crossed_uses_actual_reference_and_next_obstacle(self):
        g=core.planned_geometry('LONG',112,110,105,[(1,111),(2,125)])
        self.assertEqual(g['planned_entry'],112)
        self.assertEqual(g['planned_obstacle'],125)
        self.assertAlmostEqual(g['planned_risk_pct'],100*7/112)
        g=core.planned_geometry('SHORT',88,90,92,[(1,89),(2,75)])
        self.assertEqual(g['planned_entry'],88)
        self.assertEqual(g['planned_obstacle'],75)

    def test_unknown_room_remains_unknown(self):
        self.assertIsNone(core.planned_geometry('LONG',100,101,97,[])['planned_room_pct'])

    def test_assess_rejects_watch_that_becomes_too_risky_at_trigger(self):
        f=fixtures.ready_frames()
        for b in f['15m'][-9:-1]:b[2]=110
        p=fixtures.Decisions().assess(f=f)
        self.assertEqual(p['reason'],'PLANNED_STOP_TOO_DISTANT')
        self.assertEqual(p['stage'],'REJECTED')
        self.assertEqual(p['planned_entry'],110)

    def test_assess_rejects_watch_when_room_shrinks_at_trigger(self):
        f=fixtures.ready_frames()
        for b in f['15m'][-9:-1]:b[2]=104
        with patch.object(core,'pivots',return_value=[(1,113)]):
            p=fixtures.Decisions().assess(f=f)
        self.assertEqual(p['reason'],'PLANNED_ROOM_BLOCKED')
        self.assertLess(p['planned_room_pct'],10)

    def test_short_assessment_uses_required_lower_entry(self):
        features=fixtures.base_features()
        features.update(price=99.,leg_gain_pct=50.,volume_1h_multiple=.5,relative_strength_4h_pp=-4.,
                        confirmed_hour_highs=[(1,105.),(2,103.)],confirmed_hour_lows=[(1,97.),(2,100.)])
        f=fixtures.ready_frames()
        for b in f['15m'][-9:-1]:b[3]=80.
        p=fixtures.Decisions().assess(f=f,features=features,price=99.2)
        self.assertEqual(p['reason'],'PLANNED_STOP_TOO_DISTANT')
        self.assertEqual(p['planned_entry'],80.)

    def test_existing_ready_entry_is_unchanged(self):
        p=fixtures.Decisions().assess()
        self.assertEqual(p['stage'],'READY')
        self.assertEqual(p['price'],p['planned_entry'])
        self.assertEqual(p['risk_pct'],p['planned_risk_pct'])


if __name__=='__main__':unittest.main()
