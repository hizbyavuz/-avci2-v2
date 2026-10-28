"""Live outage regressions: repeated health messages, idle scans and archive URLs."""
from contextlib import redirect_stdout
from io import BytesIO, StringIO
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urlsplit
import zipfile

from ta_paper.archive import Archive
from ta_paper.core import CFG, MINUTE
from ta_paper.io import Store
from ta_paper.runner import STOP, SCAN_STATE_KEY, scan, scan_poll_seconds, update_health
from ta_paper.streams import daily_rows
from test_ta_paper import NOW
from test_ta_paper_scan_recovery import FakeClient


class CooldownRecoveryTests(unittest.TestCase):
    def setUp(self):
        STOP.clear()
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'research.db'
        self.store=Store(self.path)
        self.output=redirect_stdout(StringIO());self.output.__enter__()

    def tearDown(self):
        self.store.db.close();self.tmp.cleanup();STOP.clear()
        self.output.__exit__(None,None,None)

    def test_one_outage_notice_across_pending_expired_complete_slots_and_restart(self):
        deadline=NOW+24*60*MINUTE
        gate=dict(remaining_seconds=86400,until_ms=deadline,exchange_until_ms=deadline,
                  reason='BINANCE_HTTP_418',source='EXCHANGE_RETRY_TIME')
        for i,(status,reason) in enumerate((('PENDING','SCAN_INCOMPLETE'),('EXPIRED','SCAN_EXPIRED'),
                                           ('PENDING','SCAN_INCOMPLETE'),('COMPLETE','READY'))):
            update_health(self.store,reason,gate,NOW+i*CFG.entry_ms,
                          dict(status=status,slot=100+i,assessed=309,universe=315))
            self.store.db.close();self.store=Store(self.path)
        messages=[r[0] for r in self.store.db.execute('SELECT text FROM outbox')]
        self.assertEqual(len(messages),1)
        self.assertIn('erişim engeli',messages[0])
        self.assertIn('En erken yeniden deneme',messages[0])
        self.assertNotIn('Tarama tamamlandı',messages[0])
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM events WHERE kind='RUNTIME_HEALTH'").fetchone()[0],4)
        update_health(self.store,'READY',dict(gate,remaining_seconds=0),deadline+1,
                      dict(status='COMPLETE',slot=200,assessed=315,universe=315))
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0],2)
        self.assertEqual(self.store.setting('notification_health_class_v2'),'READY')

    def test_idle_progress_is_not_rewritten_or_relogged_and_expiry_is_retained(self):
        client=FakeClient(fail_symbol='S001USDT')
        first=scan(self.store,client)
        self.assertEqual(first['assessed'],1)
        self.store.db.close();self.store=Store(self.path)
        before=self.store.db.total_changes
        with patch('ta_paper.runner.log') as logged:
            for _ in range(40):scan(self.store,client)
            logged.assert_not_called()
        self.assertEqual(self.store.db.total_changes,before)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM events WHERE kind='SCAN_PROGRESS'").fetchone()[0],1)
        client.stamp=first['deadline_ms']+1
        self.assertEqual(scan(self.store,client)['status'],'EXPIRED')
        scan(self.store,client)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM events WHERE kind='SCAN_HEALTH'").fetchone()[0],1)

    def test_waiting_work_uses_bounded_polling_and_due_work_resumes_quickly(self):
        client=FakeClient(fail_symbol='S001USDT')
        scan(self.store,client)
        state=json.loads(self.store.setting(SCAN_STATE_KEY))
        client.stream_ready=lambda:True
        state['retry_at']['S002USDT']=client.stamp+MINUTE
        self.assertEqual(scan_poll_seconds(state,client),15)
        client.stamp+=MINUTE
        self.assertEqual(scan_poll_seconds(state,client),1)
        state['status']='EXPIRED'
        self.assertEqual(scan_poll_seconds(state,client),15)

    def test_short_admission_wait_wakes_at_cooldown_boundary(self):
        client=FakeClient(n=1)
        client.stream_ready=lambda:True
        client.gate.state=lambda:dict(remaining_seconds=3,until_ms=client.stamp+3000)
        state=dict(status='PENDING',initialized=True,markets={'S000USDT':{}},
                   done={'S000USDT':{'status':'SETUP'}},handled={},retry_at={},deadline_ms=client.stamp+6000)
        self.assertEqual(scan_poll_seconds(state,client),3)

    def test_unicode_archive_download_is_ascii_on_wire_with_unchanged_cache_key(self):
        day=NOW//86400000-1;stamp=day*86400000
        csv=f'{stamp},1,2,1,2,10,{stamp+MINUTE-1}\n'
        buffer=BytesIO()
        with zipfile.ZipFile(buffer,'w') as z:z.writestr('data.csv',csv)
        raw=buffer.getvalue();checksum=hashlib.sha256(raw).hexdigest().encode()
        for symbol in ('哈基米USDT','币安人生USDT','我踏马来了USDT','BTCUSDT'):
            archive=Archive(Path(self.tmp.name)/symbol)
            with patch.object(archive,'request',side_effect=[checksum,raw]) as get:
                expected=[[stamp,1.,2.,1.,2.,10.,stamp+MINUTE-1]]
                self.assertEqual(daily_rows(archive,symbol,'1m',day),expected)
                for call in get.call_args_list:
                    url=call.args[0]
                    url.encode('ascii')
                    self.assertEqual(urlsplit(url).netloc,'data.binance.vision')
                    self.assertIn('/'+symbol+'/',unquote(url))
                self.assertEqual(daily_rows(archive,symbol,'1m',day),expected)
                self.assertEqual(get.call_count,2)


if __name__=='__main__':unittest.main()
