import contextlib
import io
import json
import tempfile
import unittest
from unittest.mock import Mock, patch

import long_short_runtime_profile as profile
import long_short_futures_kline_shadow as kline
import long_short_futures_history_worker as history
from long_short_chart_first import CONFIG, assess


class ChartFeedCoverageTests(unittest.TestCase):
    def test_full_chart_feed_covers_nonrotating_symbols_over_old_cap(self):
        rows = [(f'C{i}USDT', 30_000_000, 1, 0) for i in range(120)]
        rows += [('LOWUSDT', CONFIG['min_quote_volume']-1, 1, 0)]
        with tempfile.TemporaryDirectory() as root, \
             patch('long_short_chart_first_worker.root', return_value=root), \
             patch('long_short_chart_first_worker.active_symbols', return_value=['ACTIVEUSDT']), \
             patch('long_short_analyst.native_futures_websocket_universe', return_value=rows) as universe, \
             patch.object(profile, 'enabled', return_value=True), \
             contextlib.redirect_stdout(io.StringIO()):
            requested = kline.requested_symbols('/retired.db')
        universe.assert_called_once_with(all_eligible=True, quiet=True)
        self.assertEqual(requested[0], 'ACTIVEUSDT')
        self.assertTrue(set(r[0] for r in rows[:-1]).issubset(requested))
        self.assertNotIn('LOWUSDT', requested)
        stream = kline.FuturesKlineStream(':memory:', max_symbols=kline.CHART_MAX_SYMBOLS)
        target, uncovered = stream.plan(requested)
        self.assertEqual(uncovered, [])
        self.assertEqual(target, requested)
        self.assertLessEqual(len(target)*len(kline.INTERVALS), kline.MAX_STREAMS_PER_CONNECTION)

    def test_overflow_is_explicit_and_active_idea_retained(self):
        rows = [(f'C{i}USDT', 30_000_000, 1, 0) for i in range(180)]
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as root, \
             patch('long_short_chart_first_worker.root', return_value=root), \
             patch('long_short_chart_first_worker.active_symbols', return_value=['ACTIVEUSDT']), \
             patch('long_short_analyst.native_futures_websocket_universe', return_value=rows), \
             contextlib.redirect_stdout(output):
            requested, urgent = profile.demand(kline.CHART_MAX_SYMBOLS, full_chart=True)
        event = json.loads(output.getvalue().split(' ', 1)[1])
        self.assertEqual(requested[0], 'ACTIVEUSDT')
        self.assertEqual(urgent, ['ACTIVEUSDT'])
        self.assertEqual(len(requested), kline.CHART_MAX_SYMBOLS)
        self.assertIn('C179USDT', event['deferred'])
        self.assertEqual(event['requested'], len(requested)+len(event['deferred']))

    def test_history_repair_does_not_silently_cut_at_ninety(self):
        requested = [f'C{i}USDT' for i in range(120)]
        stop = Mock()
        stop.is_set.return_value = False
        with patch.object(history, 'requested_symbols', return_value=requested), \
             patch.object(history, 'needs_repair', return_value=True), \
             patch.object(history, 'repair') as repair, \
             patch.object(history.time, 'monotonic', return_value=100), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(history.priority_pass('db', 'relay', 'retired', stop))
        covered = {(c.args[2], c.args[3]) for c in repair.call_args_list}
        for symbol in requested:
            for interval in kline.INTERVALS:
                self.assertIn((symbol, interval), covered)

    def test_low_volume_is_not_mislabeled_as_feed_failure(self):
        self.assertEqual(assess('LOWUSDT', {}, None, 1, 1000)['reason'], 'LIQUIDITY_FLOOR')
        self.assertEqual(assess('OKUSDT', {}, None, 30_000_000, 1000)['reason'], 'FRESH_PRICE_UNAVAILABLE')


if __name__ == '__main__':
    unittest.main()
