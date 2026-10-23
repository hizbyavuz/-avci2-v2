"""Synthetic presentation contract tests, not a historical market regression."""
import copy
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from technical_observer_v1 import observe, DEFAULTS
from technical_observer_v1.engine import frame_context
from technical_observer_v1.records import (
    build_record, capture_price_filter, price_grid, rounded_price, read_record, write_record,
    validate_record, record_observation,
)
from technical_observer_v1.presentation import message, render
from tests.test_technical_observer_definitions import frames, zone


def fixture():
    f, now = frames()
    f['15m'][-2].update(open=101, close=101, high=102, low=100)
    f['15m'][-1].update(open=100.1, close=99.9, high=101, low=99, quote_volume=10_000)
    broken = zone('broken', 100.013, 102.033, 'SUPPORT')
    target = zone('target', 80.018, 85.037, 'SUPPORT', '1h')
    def context(rows, tf, cfg):
        c = frame_context(rows, tf, cfg)
        c['regions'] = [broken] if tf == '15m' else [target] if tf == '1h' else []
        return c
    with patch('technical_observer_v1.engine.frame_context', side_effect=context):
        o = observe('SYNTHETIC', f, now, provenance='SYNTHETIC_UNIT_TEST_ONLY')
    assert len(o['setups']) == 1
    metadata = capture_price_filter(dict(symbols=[dict(symbol='SYNTHETIC', pricePrecision=1,
        filters=[dict(filterType='PRICE_FILTER', minPrice='0.01', maxPrice='1000', tickSize='0.05')])]),
        'SYNTHETIC', now-1000)
    return o, f, metadata


class PresentationTests(unittest.TestCase):
    def test_batch_keeps_weak_low_r_and_missing_target_records(self):
        o, f, m = fixture()
        second = copy.deepcopy(o['setups'][0]); second['id'] = 'second-synthetic-setup'
        second['target'].update(price=None, region=None)
        o['setups'][0]['stop']['price'] = 150
        o['setups'].append(second)
        with tempfile.TemporaryDirectory() as folder:
            paths = record_observation(o, f, folder, m)
            self.assertEqual(len(paths), 2)
            records = [read_record(path) for path in paths]
            self.assertEqual(records[0]['display']['state'], 'R/R YETERSİZ')
            self.assertIsNone(records[1]['display']['levels']['target'])
            self.assertTrue(all(r['decision_effect'] == 'NONE' for r in records))

    def test_non_power_of_ten_tick_and_origin_not_decimal_places(self):
        o, f, m = fixture()
        g = price_grid(m, o['symbol'], o['asof_ms'])
        self.assertEqual(rounded_price('100.013', g, 'DOWN'), '100.01')
        self.assertEqual(rounded_price('100.013', g, 'UP'), '100.06')
        r = build_record(o, o['setups'][0]['id'], f, m)
        for value in r['display']['levels'].values():
            if value is not None:
                self.assertEqual((Decimal(value)-Decimal('.01')) % Decimal('.05'), 0)

    def test_band_entry_and_target_share_edges_in_both_directions(self):
        o, f, m = fixture()
        s = o['setups'][0]
        a = build_record(o, s['id'], f, m)['display']
        self.assertEqual(a['levels']['entry'], a['broken_region']['low'])
        self.assertEqual(a['levels']['target'], a['target_region']['high'])
        # A declared synthetic opposite-direction presentation scenario.
        s.update(direction='LONG', entry_reference=s['broken_region']['high'])
        s['stop']['price'] = 98.4
        s['target']['price'] = 120.013
        s['target']['region'].update(low=120.013, high=125.013)
        b = build_record(o, s['id'], f, m)['display']
        self.assertEqual(b['levels']['entry'], b['broken_region']['high'])
        self.assertEqual(b['levels']['target'], b['target_region']['low'])
        self.assertLess(Decimal(b['levels']['stop']), Decimal('98.4'))

    def test_missing_future_wrong_symbol_and_disabled_tick_never_guessed(self):
        o, f, m = fixture()
        bad = [None, dict(m, observed_ms=o['asof_ms']+1), dict(m, symbol='OTHER'),
               dict(m, price_filter=dict(m['price_filter'], tickSize='0'))]
        for value in bad:
            r = build_record(o, o['setups'][0]['id'], f, value)
            self.assertEqual(r['grid']['status'], 'DOĞRULANMADI')
            self.assertIn('Tick size doğrulanmadı', message(r))
            self.assertTrue(r['display']['price_labels']['entry'].startswith('≈'))

    def test_r_uses_display_prices_and_preserves_raw_values(self):
        o, f, m = fixture()
        original = copy.deepcopy(o)
        r = build_record(o, o['setups'][0]['id'], f, m)
        d = r['display']; e, st, tp = [Decimal(d['levels'][k]) for k in ('entry', 'stop', 'target')]
        expected = ((e-tp)*100/e-Decimal('.30'))/((st-e)*100/e+Decimal('.30'))
        self.assertEqual(Decimal(d['net_r']['value']), expected)
        self.assertEqual(o, original)
        self.assertEqual(r['evidence']['setup'], original['setups'][0])
        self.assertEqual(DEFAULTS.fingerprint(), 'ba3f99e9804c6eaa4c508a78e3aaaee028de28ac26642de504d550de4b29f6f3')

    def test_low_r_never_says_wait_for_close(self):
        o, f, m = fixture()
        o['setups'][0]['stop']['price'] = 150
        r = build_record(o, o['setups'][0]['id'], f, m)
        text = message(r)
        self.assertEqual(r['display']['state'], 'R/R YETERSİZ')
        self.assertIn('Kırılsa da R/R yetersiz:', text)
        self.assertNotIn('kapanış bekleniyor', text.lower())
        self.assertEqual(r['decision_effect'], 'NONE')
        self.assertFalse(r['order_authorized'])

    def test_no_target_is_not_fabricated(self):
        o, f, m = fixture()
        o['setups'][0]['target'].update(price=None, region=None)
        r = build_record(o, o['setups'][0]['id'], f, m)
        self.assertIsNone(r['display']['levels']['target'])
        self.assertIsNone(r['display']['net_r']['value'])
        self.assertIn('Onaylı hedef yok; TP üretilmedi.', message(r))

    def test_rounding_overlap_records_issue_without_deleting_setup(self):
        o, f, m = fixture()
        s = o['setups'][0]
        s['target']['price'] = 99.999
        s['target']['region'].update(low=99.98, high=99.999)
        r = build_record(o, s['id'], f, m)
        self.assertIn('ROUNDING_INVALIDATED_TARGET_REGION_OR_MINIMUM_DISTANCE', r['display']['issues'])
        self.assertIsNone(r['display']['levels']['target'])
        self.assertEqual(r['evidence']['setup']['target']['price'], 99.999)
        self.assertEqual(len(o['setups']), 1)

    def test_price_bounds_do_not_clamp_or_invent_level(self):
        o, f, m = fixture()
        m['price_filter']['maxPrice'] = '103'
        r = build_record(o, o['setups'][0]['id'], f, m)
        self.assertIsNone(r['display']['levels']['stop'])
        self.assertIn('STOP:PRICE_OUTSIDE_FILTER_BOUNDS', r['display']['issues'])

    def test_different_frames_or_region_edge_cannot_be_silently_rendered(self):
        o, f, m = fixture()
        other = copy.deepcopy(f); other['15m'][-1]['close'] += .01
        with self.assertRaisesRegex(ValueError, 'FRAMES_DO_NOT_MATCH'):
            build_record(o, o['setups'][0]['id'], other, m)
        o['setups'][0]['entry_reference'] += .1
        with self.assertRaisesRegex(ValueError, 'ENTRY_NOT_THE_BROKEN_REGION_EDGE'):
            build_record(o, o['setups'][0]['id'], f, m)

    def test_append_only_round_trip_and_corruption_no_overwrite(self):
        o, f, m = fixture(); r = build_record(o, o['setups'][0]['id'], f, m)
        with tempfile.TemporaryDirectory() as folder:
            path = write_record(r, folder)
            self.assertEqual(read_record(path), r)
            before = path.stat().st_mtime_ns
            self.assertEqual(write_record(r, folder), path)
            self.assertEqual(path.stat().st_mtime_ns, before)
            path.write_text('corrupt existing record')
            with self.assertRaisesRegex(ValueError, 'EXISTING_RECORD_DIFFERS'):
                write_record(r, folder)
            self.assertEqual(path.read_text(), 'corrupt existing record')

    def test_changed_display_fails_integrity_before_message_or_chart(self):
        o, f, m = fixture(); r = build_record(o, o['setups'][0]['id'], f, m)
        r['display']['levels']['stop'] = '1'
        for operation in (validate_record, message):
            with self.assertRaisesRegex(ValueError, 'INTEGRITY'):
                operation(r)

    def test_saved_json_text_chart_share_prices_and_digest(self):
        import matplotlib.axes
        from PIL import Image
        o, f, m = fixture(); r = build_record(o, o['setups'][0]['id'], f, m)
        drawn = []
        original = matplotlib.axes.Axes.axhline
        def line(ax, value, *args, **kwargs):
            drawn.append(value)
            return original(ax, value, *args, **kwargs)
        with tempfile.TemporaryDirectory() as folder:
            saved = read_record(write_record(r, folder))
            with patch.object(matplotlib.axes.Axes, 'axhline', line):
                path = render(saved, Path(folder)/'test.png')
            self.assertEqual(set(drawn), {float(saved['display']['levels'][k]) for k in ('entry', 'stop', 'target')})
            with Image.open(path) as im:
                self.assertEqual(im.info['RecordHash'], saved['record_hash'])
            text = message(saved)
            for k in ('entry', 'stop', 'target'):
                self.assertIn(saved['display']['price_labels'][k], text)


if __name__ == '__main__':
    unittest.main()
