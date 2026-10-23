"""Step 4: explicit synthetic oracles and deterministic property checks.

Synthetic fixtures are deliberately labelled; none is a MAGMA reconstruction.
The separate MAGMA tests require the original production decision snapshot.
"""
from copy import deepcopy
from decimal import Decimal
import gzip
import json
import os
from pathlib import Path
import random
import unittest
from unittest.mock import patch

from technical_observer_v1 import DEFAULTS, observe
from technical_observer_v1.engine import (
    PERIODS, atr, frame_context, regions, stop_reference, structure, swings, target_reference,
)
from technical_observer_v1.records import build_record, capture_price_filter, read_record
from tests.test_technical_observer_definitions import START, frames, point, zone


def known_path():
    """Hand-declared extrema, unlike an oracle derived from the engine output."""
    knots = [(0, 100), (20, 100), (24, 110), (32, 90), (40, 107),
             (48, 92), (56, 104), (64, 95), (72, 105), (76, 100)]
    values = []
    for (start, a), (end, b) in zip(knots, knots[1:]):
        values.extend(a+(b-a)*(i-start)/(end-start) for i in range(start, end))
    values.append(knots[-1][1])
    return [dict(open_ms=START+i*PERIODS['15m'], close_ms=START+(i+1)*PERIODS['15m']-1,
                 open=c, high=c+.125, low=c-.125, close=c, quote_volume=100.)
            for i, c in enumerate(values)]


def random_frames(seed, n=240):
    """Binary-exact OHLC offsets. Seeded test inputs, not generated market history."""
    rng = random.Random(seed)
    end = START+2000*PERIODS['4h']
    result = {}
    for tf, step in PERIODS.items():
        rows = []; price = 200.
        for i in range(n):
            op = price; price += rng.choice([-4, -2, -1, 0, 1, 2, 4])
            rows.append(dict(open_ms=end-(n-i)*step, close_ms=end-(n-i-1)*step-1,
                open=op, close=price, high=max(op, price)+rng.choice([.125, .25, .5]),
                low=min(op, price)-rng.choice([.125, .25, .5]), quote_volume=float(rng.randrange(1, 30)*100)))
        result[tf] = rows
    return result, end


def reflect(frames_, axis=500):
    return {tf: [dict(b, open=axis-b['open'], close=axis-b['close'],
                         high=axis-b['low'], low=axis-b['high']) for b in rows]
            for tf, rows in frames_.items()}


def member_key(z, mirrored=False):
    return tuple(sorted((p['open_ms'], ('L' if p['kind'] == 'H' else 'H') if mirrored else p['kind'])
                        for p in z['members']))


class SwingRegressionTests(unittest.TestCase):
    def test_hand_declared_extrema_and_confirmation_candles(self):
        rows = known_path()
        found = swings(rows, '15m')['confirmed']
        expected = [('H', 24), ('L', 32), ('H', 40), ('L', 48), ('H', 56), ('L', 64), ('H', 72)]
        self.assertEqual([(p['kind'], p['i']) for p in found], expected)
        for p in found:
            self.assertEqual(p['known_ms'], rows[p['i']+3]['close_ms'])
            for count in range(p['i']+1, p['i']+4):
                self.assertNotIn(p['id'], [x['id'] for x in swings(rows[:count], '15m')['confirmed']])
            self.assertIn(p, swings(rows[:p['i']+4], '15m')['confirmed'])

    def test_every_append_preserves_all_confirmed_fields_all_timeframes(self):
        # 8 seeds × 3 TF × 180 prefixes = 4,320 prefix states, NOT independent trades.
        for seed in range(8):
            f, _ = random_frames(seed, 180)
            for tf, rows in f.items():
                previous = []
                for count in range(1, len(rows)+1):
                    with self.subTest(seed=seed, tf=tf, prefix=count):
                        current = swings(rows[:count], tf)['confirmed']
                        self.assertEqual(current[:len(previous)], previous)
                        self.assertTrue(all(p['known_ms'] <= rows[count-1]['close_ms'] for p in current))
                        previous = current

    def test_equal_highs_are_not_strict_swings(self):
        rows = known_path(); rows[25]['high'] = rows[24]['high']
        found = swings(rows, '15m')['confirmed']
        self.assertFalse(any(p['i'] in (24, 25) and p['kind'] == 'H' for p in found))

    def test_outside_bar_order_is_not_invented(self):
        rows = known_path(); rows[40].update(high=140, low=60)
        found = swings(rows, '15m')
        self.assertFalse(any(p['i'] == 40 for p in found['confirmed']))
        self.assertTrue(any(p['open_ms'] == rows[40]['open_ms'] and p['reason'] == 'AMBIGUOUS_OUTSIDE_BAR'
                            for p in found['rejected']))

    def test_opposite_distance_boundary_is_inclusive_and_small_leg_rejected(self):
        rows = known_path()
        with patch('technical_observer_v1.engine.atr', return_value=[20.25]*len(rows)):
            at_boundary = swings(rows, '15m')
        # First H=110.125 and L=89.875 are exactly 20.25 apart.
        self.assertTrue(any(p['i'] == 32 for p in at_boundary['confirmed']))
        with patch('technical_observer_v1.engine.atr', return_value=[20.5]*len(rows)):
            too_close = swings(rows, '15m')
        self.assertTrue(any(p.get('i') == 32 and p['reason'] == 'BELOW_MINIMUM_SWING_DISTANCE'
                            for p in too_close['rejected']))

    def test_warmup_and_zero_atr_do_not_invent_context(self):
        rows = known_path()[:13]
        self.assertTrue(all(v is None for v in atr(rows)))
        self.assertEqual(frame_context(rows, '15m')['regions'], [])
        for b in rows:
            b.update(open=100, high=100, low=100, close=100)
        self.assertEqual(swings(rows, '15m')['confirmed'], [])
        self.assertEqual(structure([])['label'], 'DOĞRULANMADI')


class CausalAndBreakoutTests(unittest.TestCase):
    def test_future_received_open_duplicate_and_bad_prices_never_form_setup(self):
        for mutation in ('received', 'open', 'duplicate', 'nan', 'negative_volume'):
            f, now = random_frames(7)
            if mutation == 'received': f['1h'][-1]['received_ms'] = now+1
            elif mutation == 'open': now -= 1
            elif mutation == 'duplicate': f['4h'][-2] = deepcopy(f['4h'][-3])
            elif mutation == 'nan': f['15m'][-1]['high'] = float('nan')
            else: f['15m'][-1]['quote_volume'] = -1
            result = observe('SYNTHETIC', f, now)
            self.assertEqual(result['status'], 'DATA_INVALID', mutation)
            self.assertEqual(result['setups'], [])

    def test_setup_anchors_and_regions_exist_before_trigger_candle(self):
        count = 0
        for seed in range(30):
            f, now = random_frames(seed)
            result = observe('SYNTHETIC', f, now, provenance='SYNTHETIC_PROPERTY_TEST')
            self.assertEqual(result['status'], 'OBSERVED')
            for s in result['setups']:
                count += 1; cutoff = s['breakout_bar']['open_ms']
                for c in result['decision_basis']['contexts'].values():
                    self.assertLess(c['last_close_ms'], cutoff)
                self.assertLess(s['broken_region']['known_ms'], cutoff)
                if s['stop']['anchor']:
                    self.assertLess(s['stop']['anchor']['known_ms'], cutoff)
                if s['target']['region']:
                    self.assertLess(s['target']['region']['known_ms'], cutoff)
        self.assertGreater(count, 0, 'Test must exercise actual generated setups')

    def test_body_volume_boundaries_and_exclusion_of_current_volume(self):
        f, now = frames()
        for b in f['15m'][-21:]: b['quote_volume'] = 100
        f['15m'][-2].update(open=100, close=100, high=101, low=99)
        # SHORT body=1 / range=2; volume=150 / median(previous20)=100.
        f['15m'][-1].update(open=100, close=99, high=100.5, low=98.5, quote_volume=150)
        z = zone('synthetic-boundary', 99.5, 100.5, 'SUPPORT')
        def context(rows, tf, cfg):
            out = frame_context(rows, tf, cfg)
            out['regions'] = [z] if tf == '15m' else []
            return out
        with patch('technical_observer_v1.engine.frame_context', side_effect=context):
            for volume, high, expected in [(150, 100.5, []), (149.99, 100.5, ['VOLUME_TOO_LOW']),
                                            (150, 100.501, ['BODY_TOO_SMALL'])]:
                f['15m'][-1].update(quote_volume=volume, high=high)
                out = observe('SYNTHETIC', f, now)
                self.assertEqual(len(out['setups']), 1)
                self.assertEqual(out['setups'][0]['breakout']['reasons'], expected)
                self.assertEqual(out['setups'][0]['breakout']['prior_volume_median'], 100)
                self.assertEqual(out['setups'][0]['decision_effect'], 'NONE')
            for b in f['15m'][-21:-1]: b['quote_volume'] = 0
            out = observe('SYNTHETIC', f, now)
            self.assertIn('VOLUME_BASELINE_UNDEFINED', out['setups'][0]['breakout']['reasons'])

    def test_wick_touch_and_already_broken_level_are_not_new_crossings(self):
        f, now = frames(); z = zone('synthetic-no-cross', 100, 102, 'SUPPORT')
        def context(rows, tf, cfg):
            out = frame_context(rows, tf, cfg); out['regions'] = [z] if tf == '15m' else []
            return out
        with patch('technical_observer_v1.engine.frame_context', side_effect=context):
            for previous, current in [(101, 100), (101, 100.5), (99, 98)]:
                for b, close in [(f['15m'][-2], previous), (f['15m'][-1], current)]:
                    b.update(open=close, close=close, high=close+1, low=close-1)
                self.assertEqual(observe('SYNTHETIC', f, now)['setups'], [])

    def test_later_extreme_candles_cannot_mutate_retained_observation(self):
        f, now = random_frames(3)
        old = observe('SYNTHETIC', f, now)
        original = json.dumps(old, sort_keys=True)
        for tf, rows in f.items():
            for n in range(1, 5):
                last = rows[-1]
                rows.append(dict(open_ms=last['open_ms']+PERIODS[tf], close_ms=last['close_ms']+PERIODS[tf],
                    open=100, high=450, low=1, close=400, quote_volume=999999))
        self.assertEqual(json.dumps(old, sort_keys=True), original)
        self.assertEqual(observe('SYNTHETIC', f, now)['status'], 'DATA_INVALID')


class GeometryAndSymmetryTests(unittest.TestCase):
    def test_wide_stop_tag_uses_strictly_greater_than_three_atr(self):
        p = point('H',102.7,40)
        exact = stop_reference('SHORT',[p],100,1)
        self.assertEqual(exact['distance_atr'],3)
        self.assertNotIn('STOP_TOO_WIDE',exact['labels'])
        p['price'] += .001
        wide = stop_reference('SHORT',[p],100,1)
        self.assertIn('STOP_TOO_WIDE',wide['labels'])
        self.assertEqual(wide['decision_effect'],'NONE')

    def test_older_range_extreme_does_not_replace_nearest_confirmed_stop(self):
        # Explicit synthetic range example; numeric resemblance is NOT MAGMA evidence.
        pts = [point('H', 150, 20), point('L', 90, 30), point('H', 103, 40)]
        stop = stop_reference('SHORT', pts, 100, 1)
        self.assertEqual(stop['price'], 103.3)
        self.assertEqual(stop['anchor']['i'], 40)
        self.assertNotEqual(stop['price'], 150.3)
        # Absence of a newer confirmed high cannot justify fabricating a closer one.
        old_only = stop_reference('SHORT', pts[:2], 100, 1)
        self.assertEqual(old_only['price'], 150.3)
        self.assertIn('STOP_TOO_WIDE', old_only['labels'])

    def test_target_overlap_exact_edge_minimum_distance_and_no_target(self):
        broken = zone('broken', 100, 102, 'SUPPORT')
        candidates = [broken, zone('edge', 99, 100, 'SUPPORT'), zone('near', 99, 99.5, 'SUPPORT'),
                      zone('exact-atr', 97, 99, 'SUPPORT', '4h')]
        target = target_reference('SHORT', broken, candidates, 100, 1)
        self.assertEqual(target['price'], 99)
        self.assertEqual(target['distance_atr'], 1)
        self.assertEqual(len(target['rejected_regions']), 3)
        self.assertIsNone(target_reference('SHORT', broken, candidates[:-1], 100, 1)['price'])

    def test_randomized_reflection_of_swings_regions_roles_strength_and_setups(self):
        setups, targets = 0, 0
        opposite = {'LONG':'SHORT', 'SHORT':'LONG', 'H':'L', 'L':'H',
                    'SUPPORT':'RESISTANCE', 'RESISTANCE':'SUPPORT',
                    'YÜKSELİŞ':'DÜŞÜŞ', 'DÜŞÜŞ':'YÜKSELİŞ', 'YATAY':'YATAY', 'DOĞRULANMADI':'DOĞRULANMADI'}
        for seed in range(30):
            f, now = random_frames(seed); mirrored = reflect(f)
            a = observe('SYNTHETIC', f, now); b = observe('SYNTHETIC_MIRROR', mirrored, now)
            with self.subTest(seed=seed):
                for tf in PERIODS:
                    ca, cb = a['contexts'][tf], b['contexts'][tf]
                    self.assertEqual(cb['structure']['label'], opposite[ca['structure']['label']])
                    self.assertAlmostEqual(ca['atr'], cb['atr'])
                    pa, pb = ca['swings']['confirmed'], cb['swings']['confirmed']
                    self.assertEqual(len(pa), len(pb))
                    for p, q in zip(pa, pb):
                        self.assertEqual((p['i'], p['known_ms'], opposite[p['kind']]), (q['i'], q['known_ms'], q['kind']))
                        self.assertAlmostEqual(p['price']+q['price'], 500)
                    zb = {member_key(z, True):z for z in cb['regions']}
                    self.assertEqual(len(ca['regions']), len(zb))
                    for z in ca['regions']:
                        other = zb[member_key(z)]
                        self.assertAlmostEqual(z['low']+other['high'], 500)
                        self.assertAlmostEqual(z['high']+other['low'], 500)
                        self.assertEqual(other['role'], opposite[z['role']])
                        self.assertEqual(z['strength']['touch_count'], other['strength']['touch_count'])
                        self.assertAlmostEqual(z['strength']['total'], other['strength']['total'])
                sb = {(s['direction'], s['broken_region_timeframe'], member_key(s['broken_region'], True)):s for s in b['setups']}
                self.assertEqual(len(a['setups']), len(sb))
                for s in a['setups']:
                    setups += 1
                    t = sb[(opposite[s['direction']], s['broken_region_timeframe'], member_key(s['broken_region']))]
                    self.assertAlmostEqual(s['entry_reference']+t['entry_reference'], 500)
                    self.assertEqual(s['breakout'], t['breakout'])
                    for key in ('stop', 'target'):
                        if s[key]['price'] is None:
                            self.assertIsNone(t[key]['price'])
                        else:
                            self.assertAlmostEqual(s[key]['price']+t[key]['price'], 500)
                    if s['target']['price'] is not None: targets += 1
                    self.assertEqual(s['late']['reasons'], t['late']['reasons'])
                    for result in (s, t):
                        r = result['net_r']
                        if r['value'] is not None:
                            self.assertAlmostEqual(r['value'], (r['reward_pct']-.3)/(r['risk_pct']+.3))
        self.assertGreater(setups, 0)
        self.assertGreater(targets, 0)

    def test_render_record_tick_rounding_reflects_on_compatible_grid(self):
        pairs = 0
        for seed in range(20):
            f, now = random_frames(seed); ff = reflect(f)
            a, b = observe('SYNTHETIC', f, now), observe('SYNTHETIC', ff, now)
            metadata = capture_price_filter(dict(symbols=[dict(symbol='SYNTHETIC', filters=[
                dict(filterType='PRICE_FILTER', minPrice='0', maxPrice='1000', tickSize='0.125')])]), 'SYNTHETIC', now)
            others = {(s['direction'],s['broken_region_timeframe'],member_key(s['broken_region'],True)):s for s in b['setups']}
            for s in a['setups']:
                side = 'SHORT' if s['direction']=='LONG' else 'LONG'
                t = others[(side,s['broken_region_timeframe'],member_key(s['broken_region']))]
                p = build_record(a,s['id'],f,metadata)['display']['levels']
                q = build_record(b,t['id'],ff,metadata)['display']['levels']
                for left,right in [('entry','entry'),('stop','stop'),('target','target'),('band_low','band_high')]:
                    if p[left] is None: self.assertIsNone(q[right])
                    else: self.assertEqual(Decimal(p[left])+Decimal(q[right]), Decimal(500))
                pairs += 1
        self.assertGreater(pairs,0)


class HistoricalSavedSnapshotTests(unittest.TestCase):
    def test_saved_archive_records_reproduce_exactly_from_their_closed_inputs(self):
        root = Path(__file__).resolve().parents[1]/'research/technical_observer_v1/step3-preview'
        manifest = json.loads((root/'manifest.json').read_text())
        self.assertEqual(len(manifest['examples']),2)
        for example in manifest['examples']:
            saved = read_record(root/example['record'])
            evidence = saved['evidence']
            result = observe(saved['symbol'],saved['frames'],saved['asof_ms'],
                             provenance=evidence['source_verification'])
            fresh = build_record(result,saved['setup_id'],saved['frames'])
            self.assertEqual(fresh,saved)
            for rows in saved['frames'].values():
                self.assertTrue(all(b['close_ms'] < saved['asof_ms'] for b in rows))
            self.assertIsNone(saved['grid']['snapshot'])
            self.assertEqual(saved['grid']['status'],'DOĞRULANMADI')


class MagmaOriginalSnapshotTests(unittest.TestCase):
    """Unverified unless the exact original event and candle evidence are supplied."""
    def load_original(self):
        name = os.environ.get('MAGMA_REGRESSION_SNAPSHOT')
        if not name:
            self.skipTest('DOĞRULANMADI: MAGMAUSDT 23/10/2026 17:00 özgün karar JSON/ham mumları yok')
        path = Path(name); raw = path.read_bytes()
        data = json.loads(gzip.decompress(raw) if path.suffix == '.gz' else raw)
        p, a, f = data['plan'], data['assessment'], data['frames']
        self.assertEqual((p['symbol'],p['direction']), ('MAGMAUSDT','SHORT'))
        self.assertEqual(a['setup_close_ms'], 1792763999999)  # 23 Oct 2026 14:00Z minus 1ms.
        self.assertAlmostEqual(p['stop'], .51757414, places=8)
        self.assertAlmostEqual(p['trigger'], .41855407, places=8)
        self.assertAlmostEqual(p['target'], .41696471, places=8)
        self.assertAlmostEqual(p['reference_price'], .41885, places=5)
        result = observe('MAGMAUSDT', {tf:f[tf] for tf in PERIODS}, a['asof_ms'],
                         provenance='ORIGINAL_DECISION_SNAPSHOT_SUPPLIED_FOR_REGRESSION')
        self.assertEqual(result['status'],'OBSERVED')
        setups = [s for s in result['setups'] if s['direction']=='SHORT']
        if not setups:
            self.skipTest('DOĞRULANMADI: özgün WATCH anında yeni kurallarda SHORT kırılımı yok; seviye testi çalışmadı')
        return setups

    def test_magma_stop_is_not_old_05176_range_top(self):
        for s in self.load_original():
            self.assertIsNotNone(s['stop']['price'])
            self.assertNotAlmostEqual(s['stop']['price'], .5176, places=4)

    def test_magma_target_is_outside_broken_region(self):
        for s in self.load_original():
            if s['target']['price'] is None:
                self.skipTest('DOĞRULANMADI: onaylı hedef yok; hedef geometrisi test edilemedi')
            self.assertLess(s['target']['region']['high'], s['broken_region']['low'])
            self.assertGreaterEqual(s['target']['distance_atr'],1)

    def test_magma_band_contains_breakout_reference(self):
        for s in self.load_original():
            self.assertLessEqual(s['broken_region']['low'],s['entry_reference'])
            self.assertLessEqual(s['entry_reference'],s['broken_region']['high'])


if __name__ == '__main__':
    unittest.main()
