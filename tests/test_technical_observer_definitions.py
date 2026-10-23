"""Definition checks with explicitly synthetic data, not MAGMA or trading proof."""
import copy
from dataclasses import replace
import json
import math
import unittest
from unittest.mock import patch

from technical_observer_v1 import DEFAULTS, observe
from technical_observer_v1.engine import (
    PERIODS, atr, frame_context, late_observation, net_r, regions,
    stop_reference, structure, swings,
)

START = 1767225600000  # Synthetic fixture time; no market data claim.


def bars(n=230, tf='15m', end=None):
    step = PERIODS[tf]
    start = START if end is None else end - n * step
    rows = []
    for i in range(n):
        close = 100 + 8 * math.cos(i * math.pi / 8)
        op = 100 + 8 * math.cos((i - .4) * math.pi / 8)
        rows.append(dict(open_ms=start + i * step, close_ms=start + (i + 1) * step - 1,
                         open=op, high=max(op, close) + .2, low=min(op, close) - .2,
                         close=close, quote_volume=100_000.))
    return rows


def frames():
    rows = bars()
    now = rows[-1]['close_ms'] + 1
    return {'15m': rows, **{tf: bars(230, tf, now // PERIODS[tf] * PERIODS[tf])
                           for tf in ('1h', '4h')}}, now


def point(kind, price, i, tf='15m'):
    return dict(id=f'{tf}:{kind}:{i}', kind=kind, price=price, i=i,
                open_ms=START + i * PERIODS[tf], known_ms=START + (i + 4) * PERIODS[tf] - 1,
                timeframe=tf, atr=1.)


def zone(id_, low, high, role, tf='15m'):
    return dict(id=id_, snapshot_id=id_ + ':snapshot', low=low, high=high,
                role=role, timeframe=tf, known_ms=START, members=[])


class DefinitionTests(unittest.TestCase):
    def test_declared_defaults_are_separate_and_frozen(self):
        self.assertEqual((DEFAULTS.swing_left, DEFAULTS.swing_right), (3, 3))
        self.assertEqual(DEFAULTS.zone_lookback, 200)
        self.assertNotEqual(DEFAULTS.fingerprint(), replace(DEFAULTS, swing_left=4).fingerprint())
        with self.assertRaises(ValueError):
            replace(DEFAULTS, atr_period=0)
        with self.assertRaises(ValueError):
            replace(DEFAULTS, zone_cluster_atr='0.5')

    def test_wilder_atr_seed_and_next_value(self):
        rows = [dict(open=100, close=100, low=99, high=101) for _ in range(15)]
        rows[-1]['high'] = 103
        out = atr(rows)
        self.assertTrue(all(v is None for v in out[:13]))
        self.assertEqual(out[13], 2.)
        self.assertAlmostEqual(out[14], (2 * 13 + 4) / 14)

    def test_confirmation_and_input_not_mutated(self):
        rows = bars()
        original = copy.deepcopy(rows)
        found = swings(rows, '15m')['confirmed']
        self.assertGreater(len(found), 4)
        p = found[-2]
        self.assertFalse(any(x['id'] == p['id'] for x in swings(rows[:p['i'] + 3], '15m')['confirmed']))
        self.assertTrue(any(x['id'] == p['id'] for x in swings(rows[:p['i'] + 4], '15m')['confirmed']))
        self.assertEqual(rows, original)

    def test_confirmed_list_is_prefix_stable(self):
        rows = bars()
        old = swings(rows[:-12], '15m')['confirmed']
        new = swings(rows, '15m')['confirmed']
        self.assertEqual(old, new[:len(old)])

    def test_structure_requires_both_pairs(self):
        up = [point('H', 110, 20), point('L', 95, 30), point('H', 115, 40), point('L', 98, 50)]
        self.assertEqual(structure(up)['label'], 'YÜKSELİŞ')
        down = [dict(p, price=200 - p['price'], kind='L' if p['kind'] == 'H' else 'H') for p in up]
        self.assertEqual(structure(down)['label'], 'DÜŞÜŞ')
        self.assertEqual(structure(up[:2])['label'], 'DOĞRULANMADI')
        up[-1]['price'] = 93
        self.assertEqual(structure(up)['label'], 'YATAY')

    def test_zones_cluster_and_have_minimum_width(self):
        rows = bars(230)
        points = [point('H', 110, 20), point('H', 103, 60), point('L', 103.2, 70)]
        zs = regions(rows, '15m', points)
        self.assertEqual(len(zs), 1)  # Old index 20 is outside the last 200 bars.
        self.assertEqual(len(zs[0]['members']), 2)
        self.assertGreaterEqual(zs[0]['high'] - zs[0]['low'] + 1e-12, .25 * atr(rows)[-1])
        self.assertEqual(set(zs[0]['strength']), {'total', 'touch_count', 'mean_reaction_atr',
                         'measured_reactions', 'timeframe_weight', 'recency', 'decision_effect'})

    def test_role_flip_uses_close_not_wick(self):
        rows = bars(50)
        for b in rows:
            b.update(open=99, close=99, high=100, low=98)
        p = point('H', 101, 20)
        rows[-1].update(high=105)
        self.assertEqual(regions(rows, '15m', [p])[0]['role'], 'RESISTANCE')
        rows[-1].update(close=104)
        z = regions(rows, '15m', [p])[0]
        self.assertEqual(z['role'], 'SUPPORT')
        self.assertEqual(z['role_changes'][-1]['known_ms'], rows[-1]['close_ms'])

    def test_stop_uses_latest_swing_without_older_fallback(self):
        points = [point('H', 130, 20), point('L', 90, 30), point('H', 103, 40)]
        s = stop_reference('SHORT', points, 100, 1)
        self.assertEqual(s['anchor'], points[-1])
        self.assertEqual(s['price'], 103.3)
        self.assertEqual(s['relation'], 'LH')
        self.assertIn('STOP_TOO_WIDE', s['labels'])
        points[-1]['price'] = 140
        s = stop_reference('SHORT', points, 100, 1)
        self.assertEqual(s['price'], 140.3)
        self.assertIn('NEAREST_SWING_NOT_CONFIRMED_LH', s['labels'])

    def test_target_excludes_overlap_nearby_and_missing(self):
        from technical_observer_v1.engine import target_reference
        broken = zone('broken', 100, 102, 'SUPPORT')
        close = zone('near', 99, 99.5, 'SUPPORT')
        overlap = zone('overlap', 99, 101, 'SUPPORT', '1h')
        good = zone('good', 95, 97, 'SUPPORT', '4h')
        t = target_reference('SHORT', broken, [broken, close, overlap, good], 100, 1)
        self.assertEqual(t['price'], 97)
        self.assertEqual(t['region'], good)
        self.assertEqual({x['reason'] for x in t['rejected_regions']},
                         {'BROKEN_ZONE_ITSELF', 'NOT_ENTIRELY_BEYOND_BROKEN_ZONE', 'TARGET_CLOSER_THAN_MINIMUM_ATR'})
        self.assertIsNone(target_reference('SHORT', broken, [close], 100, 1)['price'])

    def test_cost_formula_and_missing_prices(self):
        r = net_r('SHORT', 100, 102, 97)
        self.assertAlmostEqual(r['value'], 2.7 / 2.3)
        self.assertIsNone(net_r('SHORT', 100, 102, None)['value'])

    def test_open_unavailable_and_gap_data_rejected_without_lookahead(self):
        f, now = frames()
        f['15m'][-1]['received_ms'] = now + 1
        self.assertEqual(observe('SYNTHETIC', f, now)['status'], 'DATA_INVALID')
        f['15m'][-1].pop('received_ms')
        self.assertEqual(observe('SYNTHETIC', f, now - 1)['status'], 'DATA_INVALID')
        f['1h'].pop(-4)
        self.assertEqual(observe('SYNTHETIC', f, now)['status'], 'DATA_INVALID')

    def test_weak_break_remains_an_observation_and_zone_contains_entry(self):
        f, now = frames()
        f['15m'][-2].update(open=101, close=101, high=102, low=100)
        f['15m'][-1].update(open=100.1, close=99.9, high=101, low=99, quote_volume=10_000)
        broken = zone('broken', 100, 102, 'SUPPORT')
        target = zone('target', 80, 85, 'SUPPORT', '1h')
        real = frame_context
        def context(rows, tf, cfg):
            c = real(rows, tf, cfg)
            c['regions'] = [broken] if tf == '15m' else [target] if tf == '1h' else []
            return c
        with patch('technical_observer_v1.engine.frame_context', side_effect=context):
            out = observe('SYNTHETIC', f, now, provenance='SYNTHETIC_UNIT_TEST')
        self.assertEqual(len(out['setups']), 1)
        p = out['setups'][0]
        self.assertEqual(p['entry_reference'], p['broken_region']['low'])
        self.assertEqual(p['breakout']['label'], 'ZAYIF_KIRILIM')
        self.assertEqual(set(p['breakout']['reasons']), {'BODY_TOO_SMALL', 'VOLUME_TOO_LOW'})
        self.assertEqual(p['decision_effect'], 'NONE')
        self.assertNotIn('plan_eligible', p)
        json.dumps(out, allow_nan=False)

    def test_late_uses_absolute_distance_and_is_observational(self):
        rows = bars(60)
        for b in rows:
            b.update(open=100, close=100, high=100.1, low=99.9)
        rows[-1].update(close=110, high=110.1)
        out = late_observation(rows)
        self.assertEqual(out['label'], 'GEÇ')
        self.assertEqual(out['decision_effect'], 'NONE')
        mirrored = [dict(b, open=200-b['open'], close=200-b['close'], high=200-b['low'], low=200-b['high']) for b in rows]
        other = late_observation(mirrored)
        self.assertEqual(out['reasons'], other['reasons'])
        self.assertAlmostEqual(out['ema_distance_atr'], other['ema_distance_atr'])

    def test_unmocked_observation_uses_all_three_region_timeframes(self):
        f, now = frames()
        f['15m'][-2].update(open=106, close=107, high=108, low=105)
        f['15m'][-1].update(open=107, close=111, high=111.2, low=106.8, quote_volume=200_000)
        original = copy.deepcopy(f)
        a = observe('SYNTHETIC', f, now, provenance='SYNTHETIC_UNIT_TEST')
        self.assertEqual({p['broken_region_timeframe'] for p in a['setups']}, {'15m', '1h', '4h'})
        self.assertEqual(f, original)
        for p in a['setups']:
            self.assertLess(p['broken_region']['known_ms'], f['15m'][-1]['open_ms'])
            self.assertEqual(p['entry_reference'], p['broken_region']['high'])
            self.assertIsNone(p['target']['price'])
        json.dumps(a, allow_nan=False)

    def test_unmocked_reflection_preserves_geometry_not_price_based_percent_costs(self):
        f, now = frames()
        f['15m'][-2].update(open=106, close=107, high=108, low=105)
        f['15m'][-1].update(open=107, close=111, high=111.2, low=106.8, quote_volume=200_000)
        reflected = {tf: [dict(b, open=200-b['open'], close=200-b['close'], high=200-b['low'], low=200-b['high'])
                          for b in rows] for tf, rows in f.items()}
        a = observe('SYNTHETIC', f, now)
        b = observe('SYNTHETIC', reflected, now)
        self.assertEqual(len(a['setups']), len(b['setups']))
        for p, q in zip(a['setups'], b['setups']):
            self.assertEqual((p['direction'], q['direction']), ('LONG', 'SHORT'))
            self.assertAlmostEqual(p['entry_reference'], 200-q['entry_reference'])
            self.assertAlmostEqual(p['stop']['price'], 200-q['stop']['price'])
            self.assertAlmostEqual(p['broken_region']['low'], 200-q['broken_region']['high'])
            self.assertEqual(p['breakout']['label'], q['breakout']['label'])
            self.assertEqual([label.replace('_HL', '_LH') for label in p['stop']['labels']], q['stop']['labels'])


if __name__ == '__main__':
    unittest.main()
