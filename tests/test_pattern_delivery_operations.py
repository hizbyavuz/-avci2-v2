import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import long_short_futures_history_worker as history
import long_short_patterns as core
from tests.test_pattern_engine import plan


class OperationsTests(unittest.TestCase):
    def run_pass(self, error=None):
        seen = []
        def repair(db, base, symbol, interval):
            seen.append((symbol, interval))
            if error and symbol == 'BADUSDT':
                raise HTTPError(base, error, 'test', None, None)
        stop = threading.Event()
        with patch.object(history, 'requested_symbols', return_value=['BADUSDT', 'GOODUSDT']), \
             patch.object(history, 'needs_repair', return_value=True), \
             patch.object(history, 'repair', side_effect=repair), \
             patch.object(history, 'log'), patch.object(stop, 'wait'):
            failed = history.pattern_priority_pass('db', 'base', 'analyst', stop)
        return failed, seen

    def test_first_coin_gets_context_before_next_coin(self):
        failed, seen = self.run_pass()
        self.assertFalse(failed)
        self.assertLess(seen.index(('BADUSDT', '4h')), seen.index(('GOODUSDT', '1m')))
        self.assertEqual(set(iv for _,iv in seen), {'1m','15m','30m','4h'})

    def test_rate_limit_backoff_and_invalid_symbol_isolation(self):
        self.assertEqual(self.run_pass(429), (True, [('BADUSDT', '1m')]))
        failed, seen = self.run_pass(400)
        self.assertTrue(failed)
        self.assertIn(('GOODUSDT', '4h'), seen)
        self.assertNotIn(('BADUSDT', '15m'), seen)

    def test_cold_start_waits_but_unexpected_error_still_fails(self):
        with patch.dict('os.environ', {'LS_RUNTIME_PROFILE':'PATTERN_STRUCTURE_V2'}), \
             patch.object(history, 'log'), \
             patch.object(history, 'pattern_priority_pass', side_effect=ValueError('ASSET_CLASS_METADATA_MISSING')):
            self.assertTrue(history.guarded_priority_pass('db','base','analyst',threading.Event()))
        with patch.dict('os.environ', {'LS_RUNTIME_PROFILE':'PATTERN_STRUCTURE_V2'}), \
             patch.object(history, 'pattern_priority_pass', side_effect=ValueError('INVALID_SOURCE')):
            with self.assertRaisesRegex(ValueError,'INVALID_SOURCE'):
                history.guarded_priority_pass('db','base','analyst',threading.Event())

    def test_cards_have_actual_prices_and_direction_and_no_pending_close_claim(self):
        for direction, sl, tp in [('LONG','97','106'),('SHORT','103','94')]:
            p=plan(direction)
            text=core.message(p,100)
            self.assertTrue(text.startswith('🟢' if direction=='LONG' else '🔴'))
            self.assertIn('SL: '+sl,text)
            self.assertIn('TP: '+tp,text)
            self.assertIn('Giriş referansı / şu an: 100',text)
            self.assertIn('kapanışla teyit edildi',text)
            self.assertIn('üstünde' if direction=='LONG' else 'altında',text)
            self.assertLess(len(text),4096)
            p['entry_style']='RETEST'
            self.assertIn('Geri test sonrası kapanış teyidi geldi',core.message(p,100))
