import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

import requests
import long_short_message_card as card


class CardTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'LS_MESSAGE_CARD_DB':str(Path(self.tmp.name)/'cards.db'),
                                      'LS_TELEGRAM_EXPECTED_CHAT_ID':'123'})
        self.env.start()
        self.post=Mock(return_value=Mock(json=lambda:{'ok':True,'result':{'message_id':42,'chat':{'id':123}}}))

    def tearDown(self):self.env.stop();self.tmp.cleanup()

    def publish(self,source='EARLY',text='early',direction='LONG',**kwargs):
        return card.publish('1:token','123','TESTUSDT',source,text,direction,post=self.post,**kwargs)

    def test_one_send_then_edits_for_core_and_early_updates(self):
        self.publish();self.publish('CORE','core signal');self.publish(text='closed confirmation')
        self.assertEqual(self.post.call_count,3)
        self.assertTrue(self.post.call_args_list[0].args[0].endswith('/sendMessage'))
        for call in self.post.call_args_list[1:]:
            self.assertTrue(call.args[0].endswith('/editMessageText'))
            self.assertEqual(call.kwargs['json']['message_id'],42)
        self.assertIn('core signal',self.post.call_args.kwargs['json']['text'])
        self.assertIn('closed confirmation',self.post.call_args.kwargs['json']['text'])

    def test_restart_and_identical_text_do_not_duplicate(self):
        self.publish();result=self.publish()
        self.assertEqual(result['status'],'UNCHANGED');self.assertEqual(self.post.call_count,1)

    def test_wrong_chat_is_rejected_before_network(self):
        with self.assertRaises(RuntimeError):card.publish('1:token','999','TESTUSDT','EARLY','test','LONG',post=self.post)
        self.post.assert_not_called()

    def test_uncertain_initial_send_never_blindly_retries(self):
        self.post.side_effect=requests.Timeout('timeout')
        with self.assertRaisesRegex(RuntimeError,'TRANSPORT_FAILURE'):self.publish()
        self.post.side_effect=None
        with self.assertRaisesRegex(RuntimeError,'UNCERTAIN'):self.publish(text='updated')
        self.assertEqual(self.post.call_count,1)

    def test_invalid_ack_cannot_be_marked_delivered_or_resent(self):
        self.post.return_value=Mock(json=lambda:{'ok':True,'result':{'message_id':42,'chat':{'id':999}}})
        with self.assertRaisesRegex(RuntimeError,'ACK_INVALID'):self.publish()
        with self.assertRaisesRegex(RuntimeError,'UNCERTAIN'):self.publish()
        self.assertEqual(self.post.call_count,1)

    def test_rate_limit_backoff_preserves_current_message(self):
        self.publish()
        self.post.return_value=Mock(json=lambda:{'ok':False,'error_code':429,'parameters':{'retry_after':60}})
        with self.assertRaisesRegex(RuntimeError,'API_REJECTED'):self.publish(text='update')
        with self.assertRaisesRegex(RuntimeError,'BACKOFF'):self.publish(text='latest')
        self.assertEqual(self.post.call_count,2)

    def test_edit_not_modified_is_success(self):
        self.publish()
        self.post.return_value=Mock(json=lambda:{'ok':False,'description':'Bad Request: message is not modified'})
        self.assertEqual(self.publish(text='update')['status'],'ACK')

    def test_older_observation_cannot_overwrite_newer(self):
        self.publish(observed_at='2026-10-13T17:00:00+00:00')
        result=self.publish(text='old',observed_at='2026-10-13T16:00:00+00:00')
        self.assertEqual(result['status'],'STALE_SUPPRESSED');self.assertEqual(self.post.call_count,1)

    def test_new_core_direction_removes_opposite_early_idea(self):
        self.publish();self.publish('CORE','short core',direction='SHORT')
        self.assertNotIn('early',self.post.call_args.kwargs['json']['text'])

    def test_counter_direction_early_observation_is_visible_but_not_trade_confirmation(self):
        self.publish('CORE','🟢 LONG SİNYALİ | TESTUSDT')
        self.publish(text='🔴 SHORT | TESTUSDT — ERKEN KIRILIM',direction='SHORT')
        text=self.post.call_args.kwargs['json']['text']
        self.assertTrue(text.startswith('⚪ YÖNLER FARKLI'))
        self.assertIn('yeni işlem teyidi değildir',text)
        self.assertEqual(self.post.call_args.kwargs['json']['message_id'],42)

    def test_non_coin_health_messages_keep_original_route(self):
        with patch.object(card,'publish') as send:
            self.assertFalse(card.route_core_message('token','chat','MOTOR AKTİF'))
            send.assert_not_called()

    def test_existing_symbol_header_is_routed_without_changing_core_text(self):
        with patch.object(card,'publish',return_value={'status':'ACK'}) as send:
            msg='🔴 SHORT SİNYALİ | TESTUSDT\nSL: 100'
            self.assertTrue(card.route_core_message('token','123',msg))
            self.assertEqual(send.call_args.args[4],msg)

    def test_queued_watch_cannot_downgrade_recent_actual_trade_signal(self):
        self.publish('CORE','🟢 LONG SİNYALİ | TESTUSDT')
        result=self.publish('CORE','🟢 LONG İÇİN İZLE | TESTUSDT')
        self.assertEqual(result['status'],'UNCHANGED')
        self.assertEqual(self.post.call_count,1)


if __name__=='__main__':unittest.main()
