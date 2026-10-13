import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime,timezone
from pathlib import Path

import long_short_display as display
import long_short_message_card as cards


class DisplayTests(unittest.TestCase):
    def test_short_and_long_percent_returns_use_original_price(self):
        self.assertAlmostEqual(display.signed_return_pct('SHORT',100,90),10)
        self.assertAlmostEqual(display.signed_return_pct('LONG',100,110),10)
        self.assertAlmostEqual(display.signed_return_pct('SHORT',100,110),-10)
        for direction,entry,price in [('LONG',0,10),('SHORT',100,float('nan')),('UNKNOWN',100,90)]:
            with self.assertRaises(ValueError):display.signed_return_pct(direction,entry,price)

    def test_all_recorded_factors_are_one_plain_line(self):
        fields={'status':'kayıtlı','time':'20:10','trend':'yukarı','strength':57,'oi_change':3.05,
                'funding':0.0047,'taker_ratio':1.38,'account_ratio':2.25,'btc':'yukarı','coverage':'4/4 kayıtlı'}
        text=display.analysis_line(fields)
        self.assertNotIn('\n',text)
        for value in ['57/100','+3.05%','+0.0047%','1.38×','2.25×','20:10']:
            self.assertIn(value,text)
        for jargon in ['OI','Funding','Taker','hard-gate','z-score']:
            self.assertNotIn(jargon,text)

    def test_missing_values_are_not_zero_or_technical_suffixes(self):
        fields={'status':'kayıtlı','time':'20:10','trend':'belirsiz','btc':'belirsiz','coverage':'eksik'}
        text=display.analysis_line(fields)
        self.assertNotIn('yok%',text);self.assertNotIn('yok×',text);self.assertNotIn('yok/100',text)

    def test_stale_analysis_is_not_presented_as_current(self):
        with tempfile.TemporaryDirectory() as root:
            db=str(Path(root)/'analyst.db')
            with sqlite3.connect(db) as c:
                c.execute('CREATE TABLE analyses(symbol,scan_time_utc,payload_json)')
                c.execute('INSERT INTO analyses VALUES(?,?,?)',('TESTUSDT','2026-10-13T17:00:00Z','{}'))
            result=display.analysis_fields('TESTUSDT',db,datetime(2026,10,13,17,20,tzinfo=timezone.utc))
            self.assertEqual(result['status'],'eski kayıt')
            self.assertNotIn('4/4',display.analysis_line(result))

    def test_combined_message_has_one_evidence_line_and_preserves_distinct_levels(self):
        msg=cards.render({'CORE':{'text':'🟢 LONG İÇİN İZLE | TESTUSDT\nŞu an: 100\n📊 Seviye: 101 | Hacim 5 dk: %35\nBeklenen: 101 üstü kapanış\n🛡️ SL: 98\n🎯 TP1: 104',
                                 'direction':'LONG','observed_at':'2026-10-13T17:00:00Z'},
                         'EARLY':{'text':'🟢 LONG | TESTUSDT — ERKEN KIRILIM\nSon ölçülen fiyat: 100.1 | Kırılım: 100\nİlk erken uyarı: 100.05 | 20:00\n📊 Hacim 1 dk: 1.50× | Alış payı: %65\nErken kırılım gözlemi; işlem giriş teyidi değildir.',
                                  'direction':'LONG','observed_at':'2026-10-13T17:01:00Z'}})
        self.assertEqual(sum(line.startswith('📊') for line in msg.splitlines()),1)
        self.assertIn('Seviye: 101',msg);self.assertIn('Kırılım: 100',msg)
        self.assertIn('Hacim 5 dk:',msg);self.assertIn('Hacim 1 dk:',msg)
        self.assertIn('Stop: 98',msg);self.assertIn('Hedef 1: 104',msg)
        self.assertNotIn('2026-10-13T',msg)

    def test_invalidation_has_symbol_direction_and_routes_to_existing_card(self):
        from unittest.mock import patch
        with patch.object(cards,'publish',return_value={'status':'ACK'}) as send:
            self.assertTrue(cards.route_core_message('token','chat','🔴 SHORT BOZULDU | TESTUSDT\nÖnceki fikir geçersiz.'))
            self.assertEqual(send.call_args.args[2],'TESTUSDT')

    def test_unicode_verified_symbol_can_use_same_message_card(self):
        from unittest.mock import patch
        with patch.object(cards,'publish',return_value={'status':'ACK'}) as send:
            self.assertTrue(cards.route_core_message('token','chat','🟢 LONG | 币安人生USDT'))
            self.assertEqual(send.call_args.args[2],'币安人生USDT')

    def test_confirmation_message_uses_current_candle_volume_not_old_plan(self):
        from unittest.mock import patch
        import long_short_live_pool as lp
        row={'symbol':'TESTUSDT','direction':'LONG','trigger_level':100,
             'retest_low':99,'retest_high':101,'invalidation':98,'target1':103,'target2':104,
             'structure_gate_json':json.dumps({'volume_mult':0.35})}
        with patch.object(lp,'TELEGRAM_TRADE_ONLY',False),patch.object(lp,'_telegram_analysis_brief',return_value='Analiz: 20:10'):
            msg=lp.message_for(row,'CLOSE_CONFIRMED',101,100.5,{'qualified':True,'volume_mult':1.5})
        self.assertIn('normalin %150',msg)
        self.assertNotIn('normalin %35',msg)
        self.assertIn('henüz işlem sinyali değil',msg)


if __name__=='__main__':unittest.main()
