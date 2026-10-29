import fcntl
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from ta_paper import storage


class ReplayArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.run = storage.REPLAY_RUNS[0]
        self.directory = self.root/self.run
        self.directory.mkdir()
        self.path = self.directory/'replay_results.sqlite3'
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE outcomes(id INTEGER PRIMARY KEY,payload TEXT)')
            db.executemany('INSERT INTO outcomes VALUES(?,?)', [(i,'evidence'*300) for i in range(100)])
        (self.directory/'job_status.json').write_text(json.dumps({'status':'COMPLETE_WITH_GAPS'}))
        self.original = self.path.read_bytes()

    def tearDown(self):
        self.tmp.cleanup()

    def test_roundtrip_preserves_bytes_rows_and_durable_manifest(self):
        result = storage.archive_one(self.root,self.run)
        self.assertEqual(result['status'],'ARCHIVED')
        self.assertFalse(self.path.exists())
        self.assertEqual(result['outcome_rows'],100)
        with gzip.open(self.root/result['archive'],'rb') as src:
            self.assertEqual(src.read(),self.original)
        records=[json.loads(x) for x in (self.root/'storage_archive_20261029.jsonl').read_text().splitlines()]
        self.assertEqual([r['status'] for r in records],['VERIFIED_ARCHIVE_RAW_MAY_EXIST','ARCHIVED'])
        self.assertEqual(records[0]['sha256'],hashlib.sha256(self.original).hexdigest())
        self.assertEqual(storage.archive_one(self.root,self.run)['status'],'ABSENT_OR_ALREADY_ARCHIVED')

    def test_running_research_preserved(self):
        (self.directory/'job_status.json').write_text('{"status":"RUNNING"}')
        with self.assertRaisesRegex(ValueError,'JOB_NOT_TERMINAL'):
            storage.archive_one(self.root,self.run)
        self.assertEqual(self.path.read_bytes(),self.original)

    def test_unknown_directory_never_archived(self):
        with self.assertRaisesRegex(ValueError,'NOT_ALLOWLISTED'):
            storage.archive_one(self.root,'unknown')
        self.assertTrue(self.path.exists())

    def test_wal_evidence_preserved(self):
        wal=Path(str(self.path)+'-wal');wal.write_bytes(b'pending transaction evidence')
        with self.assertRaisesRegex(ValueError,'SIDECAR_PRESENT'):
            storage.archive_one(self.root,self.run)
        self.assertEqual(self.path.read_bytes(),self.original)
        self.assertEqual(wal.read_bytes(),b'pending transaction evidence')

    def test_low_disk_preserves_source(self):
        usage=shutil.disk_usage(self.root)._replace(free=storage.RESERVE_BYTES)
        with patch.object(storage.shutil,'disk_usage',return_value=usage):
            with self.assertRaisesRegex(ValueError,'INSUFFICIENT_FREE_SPACE'):
                storage.archive_one(self.root,self.run)
        self.assertEqual(self.path.read_bytes(),self.original)

    def test_full_disk_mid_compression_preserves_source_and_removes_only_own_temp(self):
        high=shutil.disk_usage(self.root)
        low=high._replace(free=storage.RESERVE_BYTES)
        with patch.object(storage.shutil,'disk_usage',side_effect=[high,low]):
            with self.assertRaisesRegex(ValueError,'FREE_SPACE_RESERVE_REACHED'):
                storage.archive_one(self.root,self.run)
        self.assertEqual(self.path.read_bytes(),self.original)
        self.assertFalse(list(self.directory.glob('*.tmp')))

    def test_corrupt_existing_archive_never_removes_original(self):
        target=self.directory/('replay_results.sqlite3.'+hashlib.sha256(self.original).hexdigest()+'.gz')
        with gzip.open(target,'wb') as out:out.write(b'corrupt')
        with self.assertRaisesRegex(ValueError,'ARCHIVE_ROUNDTRIP_MISMATCH'):
            storage.archive_one(self.root,self.run)
        self.assertEqual(self.path.read_bytes(),self.original)

    def test_manifest_failure_never_removes_original(self):
        with patch.object(storage,'record',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):storage.archive_one(self.root,self.run)
        self.assertEqual(self.path.read_bytes(),self.original)
        self.assertEqual(storage.archive_one(self.root,self.run)['status'],'ARCHIVED')

    def test_live_database_and_unknown_research_untouched(self):
        live=self.root/'research.sqlite3';live.write_bytes(b'live evidence')
        unknown=self.root/'unknown';unknown.mkdir()
        other=unknown/'replay_results.sqlite3';other.write_bytes(b'unlisted evidence')
        with patch.object(storage,'log'):storage.archive_retired_replays(self.root)
        self.assertEqual(live.read_bytes(),b'live evidence')
        self.assertEqual(other.read_bytes(),b'unlisted evidence')

    def test_held_research_lock_blocks_archival(self):
        with (self.root/'job.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):storage.archive_retired_replays(self.root)
        self.assertEqual(self.path.read_bytes(),self.original)

    def test_symlink_source_rejected(self):
        target=self.root/'live.sqlite3';self.path.replace(target);self.path.symlink_to(target)
        with self.assertRaisesRegex(ValueError,'SYMLINK_SOURCE'):
            storage.archive_one(self.root,self.run)
        self.assertEqual(target.read_bytes(),self.original)

    def test_corrupt_database_preserved(self):
        self.path.write_bytes(b'not a sqlite file')
        with self.assertRaises(sqlite3.DatabaseError):storage.archive_one(self.root,self.run)
        self.assertEqual(self.path.read_bytes(),b'not a sqlite file')


if __name__=='__main__':unittest.main()
