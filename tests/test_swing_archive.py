import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'research'/'swing_v1'))
import replay


class ArchiveTests(unittest.TestCase):
    def test_offline_missing_is_not_delisting_and_never_uses_network(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(replay,'fetch') as fetch:
            info,rows=replay.archive(Path(tmp),'BTCUSDT','2026-09',offline=True)
            self.assertEqual(info['status'],'NOT_CACHED');self.assertEqual(rows,[])
            fetch.assert_not_called()

    def test_non_ascii_archive_path_is_percent_encoded(self):
        with tempfile.TemporaryDirectory() as tmp:
            info,_=replay.archive(Path(tmp),'币安人生USDT','2026-08',offline=True)
            self.assertTrue(info['url'].isascii())
            self.assertIn('%E5%B8%81',info['url'])


if __name__=='__main__':unittest.main()
