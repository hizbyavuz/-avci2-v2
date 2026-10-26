"""Watch delivery stays independent of example-account admission and results."""
from contextlib import redirect_stdout
from io import BytesIO, StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from ta_paper.chart import render_plan
from ta_paper.core import CFG, MINUTE, descriptive_stats
from ta_paper.io import Store, Telegram
from ta_paper.observations import funnel_report
from ta_paper.runner import scan, STOP
from ta_paper.watch import publish_watches, watch_message
from test_ta_paper import NOW, filled, series
from test_ta_paper_operations import response
from test_ta_paper_scan_recovery import FakeClient


class WatchTests(unittest.TestCase):
    def setUp(self):
        STOP.clear();self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'watch.db';self.store=Store(self.path)
        self.capture=redirect_stdout(StringIO());self.capture.__enter__()

    def tearDown(self):
        self.capture.__exit__(None,None,None)
        self.store.db.close();self.tmp.cleanup();STOP.clear()

    def thin_client(self):
        c=FakeClient(n=1,setups=True);original=c.get
        c.get=lambda endpoint,**kw:(dict(E=c.now(),bids=[['104.99','100']],
            asks=[['105.01','.01'],['106','100']]) if endpoint.endswith('/depth') else original(endpoint,**kw))
        return c

    def candidate(self):
        return json.loads(self.store.db.execute('SELECT payload FROM watch_candidates').fetchone()[0])

    def test_book_rejected_setup_is_visible_without_paper_admission_or_performance(self):
        result=scan(self.store,self.thin_client())
        self.assertEqual((result['setups'],result['admitted']),(1,0))
        self.assertEqual(self.store.plans(),[])
        self.assertEqual(descriptive_stats(self.store.plans())['n'],0)
        self.assertEqual(funnel_report(self.store)['accepted'],0)
        p=self.candidate();self.assertEqual(p['risk_reason'],'BOOK_IMPACT_EXCEEDS_MODEL')
        row=self.store.db.execute("SELECT text,photo FROM outbox WHERE key LIKE 'watch:%'").fetchone()
        self.assertIn('🟢 LONG İÇİN İZLE',row['text'])
        self.assertIn('fiyat kayması',row['text']);self.assertIn('giriş/gerçekleşme onayı değildir',row['text'])
        self.assertIsNotNone(row['photo'])

    def test_full_example_portfolio_does_not_hide_technical_watch(self):
        for direction in ('LONG','SHORT'):
            p=filled(direction);p.update(id=direction,symbol=direction+'USDT');self.store.save(p)
        before=self.store.plans()
        result=scan(self.store,FakeClient(n=1,setups=True))
        self.assertEqual(result['admitted'],0)
        self.assertEqual(self.candidate()['risk_reason'],'MAX_OPEN_POSITIONS')
        self.assertEqual(self.store.plans(),before)

    def test_book_unavailable_watch_can_send_without_an_extra_exchange_request(self):
        c=FakeClient(n=1,setups=True);c.book_fail=True
        result=scan(self.store,c);calls=c.calls.copy()
        self.assertFalse(result['complete']);self.assertEqual(self.store.plans(),[])
        telegram=Telegram(self.tmp.name);telegram.token='TEST';telegram.chat='123'
        with patch('urllib.request.build_opener') as net:
            net.return_value.open.return_value=response({'ok':True,'result':{'chat':{'id':123},'message_id':1}})
            self.assertEqual(telegram.flush(self.store,c.now()),1)
            self.assertTrue(net.return_value.open.call_args.args[0].full_url.startswith('https://api.telegram.org/'))
        self.assertEqual(c.calls,calls)
        self.assertIn('doğrulanamadı',self.candidate()['risk_note'])

    def test_accepted_plan_uses_existing_card_without_duplicate_watch(self):
        result=scan(self.store,FakeClient(n=1,setups=True))
        self.assertEqual(result['admitted'],1)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM watch_candidates').fetchone()[0],0)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM outbox WHERE key LIKE 'watch:%'").fetchone()[0],0)

    def test_restart_does_not_duplicate_watch_and_recovery_supersedes_unsent_watch(self):
        c=FakeClient(n=1,setups=True);c.book_fail=True
        scan(self.store,c)
        self.store.db.close();self.store=Store(self.path)
        state=json.loads(self.store.setting('scan_progress_v2'))
        self.assertEqual(publish_watches(self.store,c,state),0)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM outbox WHERE key LIKE 'watch:%'").fetchone()[0],1)
        c.blocked=False;c.stamp+=MINUTE
        self.assertEqual(scan(self.store,c)['admitted'],1)
        self.assertEqual(self.store.db.execute('SELECT status FROM watch_candidates').fetchone()[0],'SUPERSEDED')
        self.assertEqual(self.store.db.execute("SELECT state FROM outbox WHERE key LIKE 'watch:%'").fetchone()[0],'STALE')

    def test_expired_watch_not_delivered_or_recreated_after_deploy(self):
        c=self.thin_client();scan(self.store,c)
        p=self.candidate();c.stamp=p['valid_until_ms']+1
        telegram=Telegram(self.tmp.name);telegram.token='TEST';telegram.chat='123'
        with patch('urllib.request.build_opener') as net:
            self.assertEqual(telegram.flush(self.store,c.now()),0);net.assert_not_called()
        self.assertEqual(self.store.db.execute("SELECT state FROM outbox WHERE key LIKE 'watch:%'").fetchone()[0],'STALE')
        state=json.loads(self.store.setting('scan_progress_v2'))
        self.assertEqual(publish_watches(self.store,c,state),0)

    def test_no_setup_or_missing_candles_cannot_become_watch(self):
        self.assertEqual(scan(self.store,FakeClient(n=1))['setups'],0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM watch_candidates').fetchone()[0],0)
        c=FakeClient(n=1,setups=True,fail_symbol='S000USDT');c.stamp+=CFG.entry_ms
        self.assertEqual(scan(self.store,c)['assessed'],0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM watch_candidates').fetchone()[0],0)

    def test_short_watch_card_needs_no_example_account_size_or_assumed_fill(self):
        scan(self.store,self.thin_client());p=self.candidate()
        p.update(direction='SHORT',reference_entry=105,entry=105,stop=107,target=101,entry_limit=104.5)
        self.assertNotIn('notional',p)
        text=watch_message(p)
        self.assertTrue(text.startswith('🔴 SHORT İÇİN İZLE'))
        self.assertIn('104.5 altı',text)
        im=Image.open(BytesIO(render_plan(p,series(CFG.entry_ms))))
        self.assertEqual(im.size,(1440,1660))


if __name__=='__main__':unittest.main()
