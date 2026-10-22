import copy
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from research.trader_v2.funding_audit import validate_report,summary
from research.swing_v1.download_required import download


class FundingAuditTests(unittest.TestCase):
    def test_partial_mode_keeps_unknowns_and_requires_explicit_flag(self):
        item=dict(symbol='TESTUSDT',interval='1m',month='2026-08',
                  url='https://data.binance.vision/test.zip')
        manifest=dict(archives=[item]);report=dict(archives=[dict(item,status='ERROR')])
        with self.assertRaisesRegex(ValueError,'EXPLICIT_PARTIAL'):
            validate_report(manifest,report,False)
        self.assertEqual(validate_report(manifest,report,True),1)
        out=summary([dict(funding_status='UNKNOWN',reason='SOURCE_ARCHIVE_MISSING'),
                     dict(funding_status='ENTRY_NOT_MEASURED')])
        self.assertEqual(out['total'],2)
        self.assertIsNone(out['mean_net_low_pct'])
        duplicate=copy.deepcopy(report);duplicate['archives']*=2
        with self.assertRaisesRegex(ValueError,'DUPLICATE'):
            validate_report(manifest,duplicate,True)

    def test_wrapped_proxy_denial_stops_further_requests(self):
        stop=threading.Event()
        item=dict(symbol='TESTUSDT',interval='1m',month='2026-08',
                  url='https://data.binance.vision/TESTUSDT-1m-2026-08.zip')
        with tempfile.TemporaryDirectory() as tmp,patch(
                'urllib.request.urlopen',side_effect=URLError('Tunnel connection failed: 403 Forbidden')) as request:
            self.assertEqual(download(item,Path(tmp),stop)['status'],'ERROR')
            self.assertTrue(stop.is_set())
            self.assertEqual(download(item,Path(tmp),stop)['status'],'CANCELLED')
            self.assertEqual(request.call_count,1)


if __name__=='__main__':unittest.main()
