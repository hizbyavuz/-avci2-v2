"""Stop-contract regressions; MERL card values are not a historical OHLC replay."""
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import long_short_patterns as core
import long_short_pattern_runtime as runtime
import long_short_pattern_outcomes as outcome
from tests.test_pattern_engine import candle, context, plan, triangle, START, T


class StopInvalidationTests(unittest.TestCase):
    def test_merl_card_stop_cannot_precede_reclaim_of_break_line(self):
        p = dict(plan('SHORT'), symbol='MERLUSDT', interval='15m',
                 entry=.0285, trigger=.028569231, stop=.028556714,
                 target=.02798, atr=.00028357)
        # Original geometry admitted this even though a return to the broken
        # line crosses the stop first. This tests the supplied card, not fills.
        self.assertFalse(core.executable(p, .0285)[0])

    def test_mirrored_long_card_has_same_guard(self):
        p = dict(plan(), entry=.0285, trigger=.028430769, stop=.028443286,
                 target=.02902, atr=.00028357)
        self.assertFalse(core.executable(p, .0285)[0])

    def test_break_stop_accounts_for_newer_closed_extreme_and_rechecks_room(self):
        rows = [candle(START+i*T, op=100.6, hi=101, lo=99, close=100.6, step=T)
                for i in range(160)]
        rows[-1].update(open=100.2, high=105., low=99.8, close=100.)
        anchor = dict(i=153, kind='H', price=101., open_ms=rows[153]['open_ms'],
                      known_ms=rows[155]['close_ms'])
        shape = dict(model='RISING_WEDGE', direction='SHORT', start_i=128,
                     boundary=(0., 100.5), stop_anchor=anchor, height=6., anchors=[anchor])
        now = rows[-1]['close_ms']+1
        with patch.object(core, '_shape_candidates', return_value=[shape]):
            ps = core._breakouts('TESTUSDT', '30m', rows, context(now), now)
        self.assertEqual(len(ps), 1)
        p = ps[0]
        self.assertGreater(p['stop'], rows[-1]['high'])
        self.assertEqual(p['stage'], 'REJECTED')
        self.assertIn('INSUFFICIENT_ROOM_AFTER_ASSUMED_COST', p['reasons'])

    def test_detected_long_and_short_breaks_allow_return_to_boundary_before_stop(self):
        rows = triangle(); now = rows[-1]['close_ms']+1
        for reflected in (False, True):
            bars = ([dict(b, open=220-b['open'], high=220-b['low'],
                          low=220-b['high'], close=220-b['close']) for b in rows]
                    if reflected else rows)
            ps = core._breakouts('TESTUSDT', '30m', bars, context(now), now)
            self.assertTrue(ps)
            for p in ps:
                sign = 1 if p['direction'] == 'LONG' else -1
                self.assertGreater(sign*(p['trigger']-p['stop']), 0)
                self.assertIn('stop_basis', p)
                self.assertIn('structure_geometry', p)

    def test_retest_stop_covers_confirmation_candle_adverse_wick(self):
        rows = triangle(); t = rows[-1]['close_ms']+1
        rows += [candle(t, op=110.1, hi=110.2, lo=109.95, close=110.08, step=T),
                 candle(t+T, op=110.1, hi=110.55, lo=109.0, close=110.4, step=T)]
        now = rows[-1]['close_ms']+1
        ps = core.assess('TESTUSDT', '30m', rows, context(now), now)
        retests = [p for p in ps if p['entry_style'] == 'RETEST']
        self.assertTrue(retests)
        self.assertTrue(all(p['stop'] < 109.0 for p in retests))

        mirrored = [dict(b, open=220-b['open'], high=220-b['low'],
                         low=220-b['high'], close=220-b['close']) for b in rows]
        ps = core.assess('TESTUSDT', '30m', mirrored, context(now), now)
        retests = [p for p in ps if p['entry_style'] == 'RETEST']
        self.assertTrue(retests)
        self.assertTrue(all(p['stop'] > 111.0 for p in retests))

    def test_legacy_merl_entry_contract_is_not_rewritten(self):
        for version in core.LEGACY_VERSIONS:
            p = dict(plan('SHORT'), version=version, entry=.0285, trigger=.028569231,
                     stop=.028556714, target=.02798, atr=.00028357)
            p.pop('stop_basis')
            saved = copy.deepcopy(p)
            s = outcome.initial(p, START, .0285)
            s = outcome.advance(p, s, [candle(START, op=.0285, hi=.02856,
                                             lo=.02849, close=.02851)])
            self.assertEqual(s['exit']['kind'], 'SL')
            self.assertEqual(s['exit']['price'], .028556714)
            self.assertEqual(p, saved)

    def test_new_plan_rejects_missing_or_modified_stop_evidence(self):
        p = plan()
        self.assertTrue(core.executable(p, 100)[0])
        for key, value in [('stop', 98.), ('trigger', 99.6), ('atr', 3.)]:
            bad = dict(p, **{key:value})
            self.assertFalse(core.executable(bad, 100)[0])
        p.pop('stop_basis')
        self.assertFalse(core.executable(p, 100)[0])

    def test_future_bar_cannot_supply_stop_extreme(self):
        rows = triangle(); now = rows[-1]['close_ms']+1
        rows.append(candle(now, op=110, hi=500, lo=1, close=110, step=T))
        with self.assertRaisesRegex(ValueError, 'FUTURE_OR_OPEN_CANDLE'):
            core.assess('TESTUSDT', '30m', rows, context(now), now)


class StopAuditTests(unittest.TestCase):
    def test_old_plans_retained_in_separate_report_cohort_and_cannot_be_republished(self):
        with tempfile.TemporaryDirectory() as directory:
            c = runtime.connect(Path(directory)/'test.db')
            try:
                old = dict(plan(), version=sorted(core.LEGACY_VERSIONS)[0], id='old')
                for p in (old, plan()):
                    c.execute('INSERT INTO plans VALUES(?,?,?,?,?,?,?,?,NULL,NULL)',
                        (p['id'],p['symbol'],p['direction'],START,START,START,'TRACKING',
                         json.dumps(p)))
                c.commit()
                before = c.execute('SELECT payload FROM plans WHERE id="old"').fetchone()[0]
                report = runtime.report(c)
                self.assertEqual(report['directions']['LONG']['notices'], 1)
                self.assertEqual(report['by_version'][old['version']]['directions']['LONG']['notices'], 1)
                self.assertEqual(runtime.publish(c,None,old,START,'abc','123'), 'POLICY_REJECTED')
                self.assertEqual(c.execute('SELECT payload FROM plans WHERE id="old"').fetchone()[0],before)
            finally:
                c.close()

    def test_candidate_audit_reconstructs_boundary_and_is_not_a_delivery(self):
        rows = triangle(); now = rows[-1]['close_ms']+1
        with tempfile.TemporaryDirectory() as directory:
            c = runtime.connect(Path(directory)/'test.db')
            output = io.StringIO()
            try:
                reader = lambda db, symbol, interval, timestamp: context(now) if interval=='4h' else rows
                with patch.object(runtime,'universe',return_value=[('TESTUSDT',3e6,110.15,0)]), \
                     patch.object(runtime,'read_bars',side_effect=reader), \
                     patch.object(runtime,'SETUP_INTERVALS',('30m',)), redirect_stdout(output):
                    runtime.scan(c,None,now)
                    runtime.scan(c,None,now)
                audits = [json.loads(line.split(' ',1)[1]) for line in output.getvalue().splitlines()
                          if line.startswith('PATTERN_CANDIDATE_AUDIT ')]
                self.assertTrue(audits)
                self.assertEqual(len(audits),c.execute('SELECT COUNT(*) FROM candidates').fetchone()[0])
                self.assertEqual(c.execute('SELECT COUNT(*) FROM plans').fetchone()[0],0)
                for p in audits:
                    self.assertEqual(p['record_type'],'ASSESSMENT_NOT_DELIVERY')
                    g = p['structure_geometry']
                    self.assertAlmostEqual(g['boundary_slope']*g['evaluated_index']+
                                           g['boundary_intercept'],p['trigger'])
                    self.assertTrue(all(a['known_ms'] < p['confirmation_ms'] for a in p['anchors']))
                    self.assertLessEqual(p['stop_basis']['window_end_ms'],p['confirmation_ms'])
            finally:
                c.close()


if __name__ == '__main__':
    unittest.main()
