import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

import long_short_message_card as cards
import long_short_breakout_observer as observer
import long_short_analyst as analyst


class EntryOnlyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'LS_TELEGRAM_TRADE_ONLY':'1',
             'LS_MESSAGE_CARD_DB':str(Path(self.tmp.name)/'cards.db'),
             'LS_TELEGRAM_EXPECTED_CHAT_ID':'123'})
        self.env.start()
        self.post=Mock(return_value=Mock(json=lambda:{'ok':True,'result':{'message_id':42,'chat':{'id':123}}}))

    def tearDown(self):self.env.stop();self.tmp.cleanup()

    def test_early_and_failed_pending_transitions_are_silent_but_retained(self):
        state=observer.initialize_state(str(Path(self.tmp.name)/'observer.db'))
        for stage in ('EARLY','CONFIRMED','CHASE','BROKEN','EXPIRED'):
            state.execute('INSERT INTO transitions(new_state,delivery_status) VALUES(?,?)',(stage,'PENDING'))
        state.execute("INSERT INTO transitions(new_state,delivery_status) VALUES('EARLY','FAILED:timeout')")
        state.commit()
        send=Mock()
        observer.deliver_pending(state,'token','chat',100000,publisher=send)
        send.assert_not_called()
        self.assertEqual(state.execute('SELECT COUNT(*) FROM transitions').fetchone()[0],6)
        self.assertEqual({r[0] for r in state.execute('SELECT delivery_status FROM transitions')},{'SILENT_POLICY'})
        state.close()

    def test_direct_early_publish_cannot_bypass_policy(self):
        result=cards.publish('1:token','123','TESTUSDT','EARLY','early','LONG',post=self.post)
        self.assertEqual(result['status'],'SILENT_POLICY');self.post.assert_not_called()

    def test_entry_gets_fresh_notification_not_edit_of_old_coin_card(self):
        with patch.dict(os.environ,{'LS_TELEGRAM_TRADE_ONLY':'0'}):
            cards.publish('1:token','123','TESTUSDT','EARLY','old early','LONG',post=self.post)
        cards.publish('1:token','123','TESTUSDT','CORE','confirmed entry','LONG',
                      message_key='TESTUSDT|LONG|setup-1',post=self.post)
        self.assertTrue(self.post.call_args.args[0].endswith('/sendMessage'))
        self.assertNotIn('old early',self.post.call_args.kwargs['json']['text'])

    def test_same_setup_is_not_resent_and_new_setup_gets_new_notification(self):
        for key in ('setup-1','setup-1','setup-2'):
            cards.publish('1:token','123','TESTUSDT','CORE','entry','LONG',message_key=key,post=self.post)
        self.assertEqual(self.post.call_count,2)
        self.assertTrue(all(c.args[0].endswith('/sendMessage') for c in self.post.call_args_list))

    def test_no_entry_identifier_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError,'ENTRY_KEY_REQUIRED'):
            cards.publish('1:token','123','TESTUSDT','CORE','watch','LONG',post=self.post)
        self.post.assert_not_called()

    def test_analyst_health_notice_is_silent_in_entry_only_mode(self):
        with patch.object(analyst,'send_telegram') as send:
            analyst.send_health_once([object()])
            send.assert_not_called()

    def test_confirmed_entry_routes_with_immutable_key(self):
        with patch.object(cards,'publish',return_value={'status':'ACK'}) as send:
            cards.route_core_message('token','123','🔴 SHORT SİNYALİ | TESTUSDT',entry_key='locked-setup')
            self.assertEqual(send.call_args.kwargs['message_key'],'locked-setup')


if __name__=='__main__':unittest.main()
