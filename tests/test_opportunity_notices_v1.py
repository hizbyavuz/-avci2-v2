import copy
import json
import os
import tempfile
import unittest
from datetime import datetime,timezone
from unittest.mock import Mock,patch
import requests
import long_short_message_card as card
import long_short_opportunity_notices as notices
from long_short_structural_plan_v6 import local_invalidation,from_candles
from long_short_native_derivatives import validate
from test_native_chain_v2 import fixture,NOW,SYMBOL
import test_local_geometry_continuity_v2 as geo

class LocalStopTests(unittest.TestCase):
    def test_latest_confirmed_intact_pivot_both_sides_and_no_mutation(self):
        p,g,z=geo.LocalGeometryTests().inputs()
        k={'low':[99,98,97,98,99,99.1,98.5,99,99.2], 'high':[101,102,103,102,101,100.9,101.5,101,100.8]}
        before=copy.deepcopy(k)
        self.assertEqual(local_invalidation(k,'LONG',100,p)['level'],98.5)
        ps,_,_=geo.LocalGeometryTests().inputs('SHORT')
        self.assertEqual(local_invalidation(k,'SHORT',100,ps)['level'],101.5)
        new,ng=from_candles(p,g,[(k,'5m',1)],100,lambda *args:z,.3,1,.9)
        self.assertEqual(k,before);self.assertEqual(p['invalidation'],98)
        self.assertEqual(new['invalidation'],98.5);self.assertEqual(new['target1'],104)
        self.assertEqual(ng['geometry_comparison']['v5']['stop'],98)
        self.assertEqual(ng['geometry_comparison']['v6']['stop'],98.5)
    def test_unconfirmed_or_broken_pivot_does_not_create_stop(self):
        p,g,z=geo.LocalGeometryTests().inputs()
        k={'low':[99,98,97,98,99,96,95], 'high':[101]*7}
        self.assertIsNone(local_invalidation(k,'LONG',100,p))
        q,gg=from_candles(p,g,[(k,'5m',1)],100,lambda *args:z,.3,1,.9)
        self.assertEqual(gg['structural_plan_v3']['reason'],'NO_LOCAL_INVALIDATION')
        self.assertFalse(gg['qualified_precheck']);self.assertEqual(q['invalidation'],0)
        self.assertEqual(q['target1'],0)

class NoticeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'LS_STATE_DIR':self.tmp.name,'LS_MESSAGE_CARD_DB':self.tmp.name+'/cards.db',
            'LS_TELEGRAM_EXPECTED_CHAT_ID':'123','LS_OPPORTUNITY_NOTICES_V1':'1','LS_QUALIFIED_SIGNAL_CHAIN_V1':'1'})
        self.env.start()
    def tearDown(self):self.env.stop();self.tmp.cleanup()
    def payload(self):
        e=validate(fixture(),SYMBOL,NOW);e.update(ready=True,taker_ratio=1.2,taker_fresh=True)
        return dict(evidence=e,direction_engine=dict(direction='LONG',best_score=55,edge=20,hard_blockers=[]),
            setup_plan=dict(direction='LONG',trigger_level=101,local_invalidation=dict(level=98,timeframe='5m',confirmation_bars=2)),
            structure_gate=dict(qualified_precheck=False,structural_plan_v3={'reason':'INSUFFICIENT_GEOMETRY'}))
    def test_opportunity_does_not_require_entry_geometry_but_keeps_data_and_direction(self):
        p=self.payload();d=notices.assess(p,100,NOW,NOW)
        self.assertTrue(d['allowed'])
        text=notices.message(SYMBOL,p,d,NOW)
        self.assertIn('Giriş bekleniyor',text);self.assertIn('işlem sinyali değil',text)
        self.assertIn('98 altında',text);self.assertNotIn('SL:',text)
        self.assertFalse(notices.assess(p,None,NOW,NOW)['allowed'])
        self.assertFalse(notices.assess(p,100,NOW,NOW+300001)['allowed'])
        p['direction_engine']['hard_blockers']=['liquidity_floor']
        self.assertFalse(notices.assess(p,100,NOW,NOW)['allowed'])
    def test_one_missing_is_named_and_two_missing_are_not_promoted(self):
        p=self.payload();p['evidence'].update(ready=False,long_short_ratio=None,ls_fresh=False,
            taker_coverage_ready=True,taker_source='BINANCE_UM_FUTURES_KLINE')
        p['direction_engine']['hard_blockers']=['derivatives_incomplete']
        d=notices.assess(p,100,NOW,NOW);self.assertTrue(d['allowed'])
        self.assertIn('Eksik veri: long/short',notices.message(SYMBOL,p,d,NOW))
        p['evidence']['funding_fresh']=False
        self.assertFalse(notices.assess(p,100,NOW,NOW)['allowed'])
    def test_one_episode_then_silent_end_no_repeated_sends(self):
        from long_short_data_opportunity_v2 import observe
        publisher=Mock(return_value={'status':'ACK'})
        with patch('long_short_data_opportunity_v2.current_price',return_value=100):
            observe(SYMBOL,'FULL_4_OF_4','LONG',self.payload(),NOW)
            notices.publish_recent(NOW,publisher,'1:token','123')
            notices.publish_recent(NOW+60000,publisher,'1:token','123')
        self.assertEqual(publisher.call_count,1)
        first=publisher.call_args.kwargs['episode_key']
        with patch('long_short_data_opportunity_v2.current_price',return_value=97):
            notices.publish_recent(NOW+120000,publisher,'1:token','123')
            notices.publish_recent(NOW+180000,publisher,'1:token','123')
        self.assertEqual(publisher.call_count,2);self.assertEqual(publisher.call_args.kwargs['episode_key'],first)
        self.assertIn('TAKİBİ SONA ERDİ',publisher.call_args.args[4])
    def test_accepted_send_then_crash_keeps_episode_key(self):
        from long_short_data_opportunity_v2 import observe
        publisher=Mock(side_effect=RuntimeError('uncertain'))
        with patch('long_short_data_opportunity_v2.current_price',return_value=100):
            observe(SYMBOL,'FULL_4_OF_4','LONG',self.payload(),NOW)
            notices.publish_recent(NOW,publisher,'1:token','123')
            key=publisher.call_args.kwargs['episode_key']
            observe(SYMBOL,'FULL_4_OF_4','LONG',self.payload(),NOW+60000)
            publisher.side_effect=None;publisher.return_value={'status':'UNCHANGED'}
            notices.publish_recent(NOW+60000,publisher,'1:token','123')
        self.assertEqual(publisher.call_args.kwargs['episode_key'],key)
    def test_card_opportunity_upgrade_sends_new_entry_notification_once(self):
        post=Mock(return_value=Mock(json=lambda:{'ok':True,'result':{'message_id':42,'chat':{'id':123}}}))
        stamp=datetime.now(timezone.utc).isoformat()
        def pub(source,text,key=None):
            return card.publish('1:token','123',SYMBOL,source,text,'LONG',observed_at=stamp,post=post,episode_key=key)
        pub('OPPORTUNITY','🟡 LONG FIRSATI | BTCUSDT\nGiriş bekleniyor.','one')
        trade='🟢 LONG SİNYALİ | BTCUSDT\n📍 Giriş referansı: 100'
        pub('CORE',trade);pub('CORE',trade)
        self.assertEqual(post.call_count,2)
        self.assertTrue(all(c.args[0].endswith('/sendMessage') for c in post.call_args_list))
        self.assertNotIn('FIRSATI',post.call_args.kwargs['json']['text'])
        self.assertEqual(pub('OPPORTUNITY','watch','two')['status'],'CORE_ACTIVE_SUPPRESSED')
    def test_uncertain_opportunity_does_not_duplicate_on_new_key(self):
        post=Mock(side_effect=requests.Timeout())
        with self.assertRaises(RuntimeError):card.publish('1:token','123',SYMBOL,'OPPORTUNITY','watch','LONG',post=post,episode_key='one')
        with self.assertRaisesRegex(RuntimeError,'UNCERTAIN'):card.publish('1:token','123',SYMBOL,'OPPORTUNITY','watch','LONG',post=post,episode_key='two')
        self.assertEqual(post.call_count,1)

if __name__=='__main__':unittest.main()
