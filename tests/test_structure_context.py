import copy
import json
import math
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from structure_engine.config import CONFIG as C, CONFIG_HASH, HOUR, MINUTE, PERIODS, VERSION
from structure_engine import core, store
from structure_engine.chart import drawing_spec, render
from structure_engine.runtime import read_frames, scan, measure_pending, notify_terminal
from structure_engine.outcomes import measure
from structure_engine.archive import aggregate

START = 1767225600000


def waves(n=160, step=15*MINUTE, end=None, center=100., amplitude=8.):
    start = START if end is None else end-n*step
    out = []
    for i in range(n):
        close = center+amplitude*math.cos(i*math.pi/8)
        op = center+amplitude*math.cos((i-.6)*math.pi/8)
        out.append(dict(open_ms=start+i*step, close_ms=start+(i+1)*step-1,
                        open=op, close=close, high=max(op, close)+.2, low=min(op, close)-.2,
                        quote_volume=100_000., taker_buy_quote=50_000.))
    return out


def fixture(kind='break', interval='15m'):
    rows = waves(step=PERIODS[interval])
    if kind == 'break':
        rows[-1].update(open=107., high=109.2, low=106.8, close=109.)
    elif kind == 'watch':
        rows[-1].update(open=106., high=107.5, low=105.8, close=107.3)
    now = rows[-1]['close_ms']+1
    frames = {interval: rows}
    for tf in ('1h', '4h'):
        end = now//PERIODS[tf]*PERIODS[tf]
        frames[tf] = waves(80, PERIODS[tf], end=end, center=100, amplitude=20)
    return frames, now


class StructureTests(unittest.TestCase):
    def test_retest_uses_original_boundary_and_does_not_widen_stop(self):
        frames, now = fixture()
        p = next(p for p in core.assess('TUSDT', frames, now)['decisions'] if p['direction'] == 'LONG')
        z = p['decision_zone']
        bar = dict(open_ms=now, close_ms=now+PERIODS['15m']-1,
                   open=z['high'], low=z['high']-.1, high=p['trigger']+.6,
                   close=p['trigger']+.4, quote_volume=100_000.)
        rows = frames['15m']+[bar]
        q = next(q for q in core._retests(rows, '15m') if q['direction'] == 'LONG')
        self.assertEqual(q['condition'], 'RETEST_HELD')
        self.assertEqual(q['decision_zone'], p['decision_zone'])
        self.assertEqual(q['stop'], p['stop'])
        rows[-1]['low'] = p['stop']-1
        self.assertFalse(core._retests(rows, '15m'))

    def test_future_open_stale_gap_and_invalid_volume_are_not_no_opportunity(self):
        frames, now = fixture()
        variants = []
        f = copy.deepcopy(frames); f['15m'][-1]['close_ms'] += PERIODS['15m']; variants.append(f)
        f = copy.deepcopy(frames); f['1h'].pop(-5); variants.append(f)
        f = copy.deepcopy(frames); f['4h'][-1]['received_ms'] = now+1; variants.append(f)
        f = copy.deepcopy(frames); f['15m'][-1]['quote_volume'] = float('nan'); variants.append(f)
        for f in variants:
            a = core.assess('TUSDT', f, now)
            self.assertEqual(a['status'], 'DATA_INVALID')
            self.assertFalse(a['decisions'])
            self.assertTrue(a['data_errors'])
        self.assertEqual(core.assess('TUSDT', frames, now+PERIODS['15m'])['status'], 'DATA_INVALID')

    def test_pivot_not_available_until_second_right_candle(self):
        rows = waves()
        full = core.significant_swings(rows)
        p = full[-2]
        before = core.significant_swings(rows[:p['i']+2])
        after = core.significant_swings(rows[:p['i']+3])
        self.assertFalse(any(x['open_ms'] == p['open_ms'] for x in before))
        self.assertTrue(any(x['open_ms'] == p['open_ms'] for x in after))
        self.assertEqual(p['known_ms'], rows[p['i']+2]['close_ms'])

    def test_small_two_bar_leg_is_not_fibonacci_anchor(self):
        rows = waves()
        ps = core.significant_swings(rows)
        for a, b in zip(ps, ps[1:]):
            self.assertGreaterEqual(b['i']-a['i'], C['significant_leg_min_bars'])
            self.assertGreaterEqual(abs(b['price']-a['price']), C['significant_leg_min_atr']*max(a['atr'], b['atr']))

    def test_watch_survives_narrow_room_without_becoming_trade_plan(self):
        frames, now = fixture('watch')
        with patch('structure_engine.core._obstacle', side_effect=lambda levels, side, price:
                   {'low':price+.1, 'high':price+.2, 'timeframe':'1h'}):
            a = core.assess('TUSDT', frames, now)
        p = next(x for x in a['decisions'] if x['direction'] == 'LONG')
        self.assertEqual(p['stage'], 'WATCH')
        self.assertFalse(p['plan_eligible'])
        self.assertIn(p['payoff']['reason'], ('COST_EXCEEDS_ROOM', 'LOW_NET_REWARD_RISK'))
        self.assertIn('İÇİN İZLE', core.message(p))

    def test_fresh_break_and_excessive_chase_are_separate(self):
        frames, now = fixture()
        a = core.assess('TUSDT', frames, now)
        p = next(x for x in a['decisions'] if x['direction'] == 'LONG')
        self.assertEqual(p['stage'], 'CONDITION_MET')
        frames['15m'][-1].update(close=120., high=121.)
        p = next(x for x in core.assess('TUSDT', frames, now)['decisions'] if x['direction'] == 'LONG')
        self.assertEqual(p['stage'], 'LATE')
        self.assertFalse(p['plan_eligible'])

    def test_short_long_reflection_has_same_geometry_and_event(self):
        frames, now = fixture()
        a = core.assess('TUSDT', frames, now)
        reflected = copy.deepcopy(frames)
        for rows in reflected.values():
            for b in rows:
                b.update(open=200-b['open'], close=200-b['close'], high=200-b['low'], low=200-b['high'])
        b = core.assess('TUSDT', reflected, now)
        p = next(p for p in a['decisions'] if p['direction'] == 'LONG')
        q = next(p for p in b['decisions'] if p['direction'] == 'SHORT')
        self.assertEqual(p['stage'], q['stage'])
        self.assertAlmostEqual(p['trigger'], 200-q['trigger'])
        self.assertAlmostEqual(p['stop'], 200-q['stop'])
        self.assertTrue(core.message(q).startswith('🔴 SHORT'))

    def test_30m_is_independent_not_extra_confirmation(self):
        frames, now = fixture(interval='30m')
        self.assertNotIn('15m', frames)
        a = core.assess('TUSDT', frames, now, '30m')
        self.assertEqual(a['status'], 'VALID')
        self.assertTrue(a['decisions'])

    def test_wrong_side_and_cost_shortfall_have_distinct_reasons(self):
        self.assertEqual(core.payoff('LONG', 100, 99, 100.2)['reason'], 'COST_EXCEEDS_ROOM')
        self.assertEqual(core.payoff('LONG', 100, 101, 103)['reason'], 'STOP_WRONG_SIDE')
        self.assertEqual(core.payoff('LONG', 100, 99, 98)['reason'], 'TARGET_WRONG_SIDE')
        self.assertEqual(core.payoff('LONG', 100, 99, None)['reason'], 'FIRST_OBSTACLE_UNKNOWN')
        self.assertAlmostEqual(core.payoff('LONG', 100, 99, 103)['net_r'], 2.7/1.3)

    def test_context_opposition_warns_without_unanimity_filter(self):
        frames, now = fixture()
        real = core.describe
        def describe(rows):
            result = real(rows)
            if len(rows) == 80: result['direction'] = 'DOWN'
            return result
        with patch('structure_engine.core.describe', side_effect=describe):
            a = core.assess('TUSDT', frames, now)
        p = next(p for p in a['decisions'] if p['direction'] == 'LONG')
        self.assertIn('AGAINST_4h_STRUCTURE', p['warnings'])
        self.assertEqual(p['scenario'], 'COUNTERTREND_CHANGE')

    def test_chart_spec_is_exact_decision_and_rejects_future_bars(self):
        frames, now = fixture()
        a = core.assess('TUSDT', frames, now)
        p = a['decisions'][0]
        spec = drawing_spec(a, p)
        self.assertEqual(spec['stop'], p['stop'])
        self.assertEqual(spec['zone'], p['decision_zone'])
        self.assertEqual(spec['fibonacci'], p['fibonacci'])
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'chart.png'
            render(a, frames, p, path)
            self.assertGreater(path.stat().st_size, 10000)
            frames['15m'].append(dict(frames['15m'][-1], close_ms=now+1))
            with self.assertRaisesRegex(ValueError, 'FUTURE_CANDLE'):
                render(a, frames, p, path)

    def test_fibonacci_maths_and_snapshot_not_mutated(self):
        frames, now = fixture()
        before = copy.deepcopy(frames)
        a = core.assess('TUSDT', frames, now)
        self.assertEqual(frames, before)
        p = a['decisions'][0]
        f = p['fibonacci']
        self.assertTrue(f)
        self.assertLess(f['end']['known_ms'], now)
        self.assertAlmostEqual(f['retracements']['0.5'], (f['start']['price']+f['end']['price'])/2)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.c = store.connect(self.path/'state.db')
        self.frames, self.now = fixture()
        self.a = core.assess('TUSDT', self.frames, self.now)
        self.p = self.a['decisions'][0]

    def tearDown(self):
        self.c.close()
        self.temp.cleanup()

    def test_snapshot_and_event_idempotency(self):
        store.save(self.c, self.a, self.frames, self.path)
        self.assertFalse(store.save(self.c, self.a, self.frames, self.path))
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM scans').fetchone()[0], 1)
        self.assertTrue(list((self.path/'snapshots').glob('*.json.gz')))

    def test_restart_cannot_change_frozen_code_manifest(self):
        with patch('structure_engine.store.code_hash', return_value='changed'):
            with self.assertRaisesRegex(ValueError, 'WITHOUT_NEW_VERSION'):
                store.connect(self.path/'state.db')

    def test_chart_and_message_receipts_and_no_blind_retry(self):
        store.save(self.c, self.a, self.frames, self.path)
        image = self.path/'chart.png'; image.write_bytes(b'fake')
        post = Mock(return_value=Mock(json=lambda: {'ok': True, 'result': {'message_id': 12, 'chat': {'id': 123}}}))
        self.assertEqual(store.publish(self.c, self.p, image, core.message(self.p), self.now, 'fake', '123', post), ['ACK','ACK'])
        store.publish(self.c, self.p, image, core.message(self.p), self.now, 'fake', '123', post)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(store.notification_reason(self.c, self.p, self.now), 'ALREADY_ATTEMPTED')

    def test_uncertain_network_never_retried(self):
        image = self.path/'chart.png'; image.write_bytes(b'fake')
        post = Mock(side_effect=TimeoutError('redacted'))
        self.assertEqual(store.publish(self.c, self.p, image, 'x', self.now, 'fake', '123', post), ['UNCERTAIN'])
        store.publish(self.c, self.p, image, 'x', self.now, 'fake', '123', post)
        self.assertEqual(post.call_count, 1)

    def test_invalidated_idea_retained_and_not_republished(self):
        store.save(self.c, self.a, self.frames, self.path)
        bar = dict(self.frames['15m'][-1], close_ms=self.now+PERIODS['15m']-1,
                   low=self.p['stop']-1, high=self.p['stop']+1)
        store.lifecycle(self.c, 'TUSDT', '15m', [bar], self.now+PERIODS['15m'])
        self.assertEqual(self.c.execute('SELECT state FROM transitions').fetchone()[0], 'INVALIDATED')
        self.assertEqual(store.notification_reason(self.c, self.p, self.now), 'IDEA_TERMINAL')
        self.assertGreater(self.c.execute('SELECT COUNT(*) FROM events').fetchone()[0], 0)

    def test_terminal_notice_is_text_only_and_idempotent(self):
        store.save(self.c, self.a, self.frames, self.path)
        image = self.path/'chart.png'; image.write_bytes(b'fake')
        post = Mock(return_value=Mock(json=lambda: {'ok': True, 'result': {'message_id': 12, 'chat': {'id': 123}}}))
        store.publish(self.c, self.p, image, 'original', self.now, 'fake', '123', post)
        self.c.execute('INSERT INTO transitions VALUES(?,?,?,?,?)', ('terminal', self.p['idea_id'], self.now, 'INVALIDATED', '{}'))
        self.c.commit()
        with patch.dict('os.environ', {'TELEGRAM_BOT_TOKEN':'fake','TELEGRAM_CHAT_ID':'123'}), patch('requests.post', post):
            notify_terminal(self.c, self.now)
            notify_terminal(self.c, self.now)
        self.assertEqual(post.call_count, 3)
        self.assertIn('FİKİR BOZULDU', post.call_args.kwargs['json']['text'])


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.path = Path(self.temp.name)
        self.c = store.connect(self.path/'state.db')
        self.source = sqlite3.connect(':memory:'); self.source.row_factory = sqlite3.Row
        self.source.execute('''CREATE TABLE futures_closed_klines(symbol TEXT,interval TEXT,open_ms INTEGER,
          close_ms INTEGER,open REAL,high REAL,low REAL,close REAL,quote_volume REAL,taker_buy_quote REAL,
          source TEXT,received_ms INTEGER)''')
        self.frames, self.now = fixture()
        for tf, rows in self.frames.items(): self.add(rows, tf)

    def add(self, rows, tf):
        for b in rows:
            self.source.execute('INSERT INTO futures_closed_klines VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                ('TUSDT', tf, b['open_ms'], b['close_ms'], b['open'], b['high'], b['low'], b['close'],
                 b['quote_volume'], b.get('taker_buy_quote',0), 'BINANCE_UM_FUTURES_ARCHIVE', b['close_ms']+1))
        self.source.commit()

    def tearDown(self):
        self.c.close(); self.source.close(); self.temp.cleanup()

    def test_db_to_snapshot_to_chart_default_sends_nothing_and_scans_once(self):
        with patch('requests.post', side_effect=AssertionError('NO_NETWORK')):
            h = scan(self.c, self.source, ['TUSDT'], self.now, self.path)
            self.assertEqual(h['scanned'], 1)
            self.assertFalse(h['issues'])
            h = scan(self.c, self.source, ['TUSDT'], self.now+10, self.path)
            self.assertEqual(h['already_scanned'], 1)
        self.assertTrue(list((self.path/'charts').glob('*.png')))
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM notices').fetchone()[0], 0)

    def test_delivered_tracking_continues_after_research_finishes(self):
        a = core.assess('TUSDT', self.frames, self.now); p = a['decisions'][0]
        store.save(self.c, a, self.frames, self.path)
        entry = self.frames['15m'][-1]['close']
        rows = [dict(open_ms=self.now+(i+1)*MINUTE, close_ms=self.now+(i+2)*MINUTE-1,
                     open=entry, high=entry+.1, low=entry-.1, close=entry, quote_volume=100_000.) for i in range(185)]
        self.add(rows, '1m')
        for part in ('chart', 'text'):
            self.c.execute('INSERT INTO notices VALUES(?,?,?,?,?)',
                (part, p['event_id'], self.now, 'ACK', json.dumps({'ack_ms':self.now+2*MINUTE,'message_id':1})))
        self.c.commit()
        first = measure_pending(self.c, self.source, self.now+181*MINUTE)
        self.assertEqual(first['RESEARCH:COMPLETE'], 1)
        self.assertEqual(first['DELIVERED:INCOMPLETE_HORIZON'], 1)
        last = measure_pending(self.c, self.source, self.now+184*MINUTE)
        self.assertEqual(last['DELIVERED:COMPLETE'], 1)

    def test_untrusted_candle_source_is_rejected(self):
        self.source.execute("UPDATE futures_closed_klines SET source='UNVERIFIED' WHERE interval='1h'")
        with self.assertRaisesRegex(ValueError,'UNVERIFIED_SOURCE'):
            read_frames(self.source, 'TUSDT', '15m', self.now)


class OutcomeTests(unittest.TestCase):
    def setUp(self):
        self.p = dict(asof_ms=START, direction='LONG', plan_eligible=True, stop=98., target=104.)
        self.rows = [dict(open_ms=START+(i+1)*MINUTE, close_ms=START+(i+2)*MINUTE-1,
                          open=100., high=100.1, low=99.9, close=100., quote_volume=100_000.,
                          taker_buy_quote=50_000.) for i in range(180)]

    def test_entry_is_strictly_after_signal_and_both_barriers_stop_first(self):
        self.rows[0].update(high=105., low=97.)
        out = measure(self.p, self.rows)
        self.assertEqual(out['reference_ms'], START+MINUTE)
        self.assertEqual(out['exit']['kind'], 'STOP_FIRST_AMBIGUOUS')
        self.assertAlmostEqual(out['fee_slippage_only_pct'], -2.3)
        self.assertIsNone(out['full_net_pct'])

    def test_gap_stop_uses_first_available_open_not_stop_price(self):
        self.rows[2].update(open=97., high=98., low=96., close=97.)
        out = measure(self.p, self.rows)
        self.assertEqual(out['exit']['kind'], 'GAP_STOP')
        self.assertAlmostEqual(out['fee_slippage_only_pct'], -3.3)

    def test_missing_minutes_remain_unresolved(self):
        self.rows.pop(2)
        out = measure(self.p, self.rows)
        self.assertEqual(out['status'], 'DATA_GAP')
        self.assertIsNone(out['horizon_fee_only_pct'])
        self.assertIsNone(out['fee_slippage_only_pct'])

    def test_watch_is_observation_not_trade_profit(self):
        self.p['plan_eligible'] = False
        self.rows[0].update(high=105., low=97.)
        out = measure(self.p, self.rows)
        self.assertEqual(out['status'], 'COMPLETE')
        self.assertIsNone(out['exit'])
        self.assertIsNone(out['fee_slippage_only_pct'])
        self.assertAlmostEqual(out['raw_mfe_pct'], 5.)

    def test_entry_already_invalid_cancels_without_inventing_loss(self):
        self.rows[0]['open'] = 97.
        out = measure(self.p, self.rows)
        self.assertTrue(out['entry_cancelled'])
        self.assertIsNone(out['exit'])

    def test_exact_aggregation_does_not_fill_a_missing_minute(self):
        rows = self.rows[14:44]
        full = aggregate(rows, '15m')
        self.assertEqual(len(full), 2)
        rows.pop(1)
        self.assertEqual(len(aggregate(rows, '15m')), 1)


if __name__ == '__main__':
    unittest.main()
