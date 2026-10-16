import gzip
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import long_short_legacy_archive as archive
import long_short_runtime_profile as profile
import long_short_compact_backup as backup

REAL_OPEN_HANDLES = archive.open_handles


def own_process_handles(path):
    # This host cannot inspect other users' processes; temp fixtures are owned
    # exclusively by this test process. Production keeps the full fail-closed scan.
    return REAL_OPEN_HANDLES(path, processes=[Path('/proc/self')])


class RetirementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        scanner = patch.object(archive, 'open_handles', side_effect=own_process_handles)
        scanner.start()
        self.addCleanup(scanner.stop)

    def tearDown(self): self.temp.cleanup()

    def database(self):
        p = self.root/'long_short_breakout_observer.db'
        c = sqlite3.connect(p)
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('CREATE TABLE events(id INTEGER, payload TEXT)')
        c.executemany('INSERT INTO events VALUES(?,?)', [(i, 'record '+str(i)) for i in range(200)])
        c.commit()
        c.close()
        return p

    def test_archive_restores_every_record_and_is_idempotent(self):
        source = self.database()
        row = archive.archive_one(self.root, source.name)
        self.assertFalse(source.exists())
        path = self.root/'legacy-archive'/row['archive']
        self.assertEqual(archive.digest_file(path, True), (row['source_sha256'], row['source_bytes']))
        restored = self.root/'restored.db'
        with gzip.open(path, 'rb') as f: restored.write_bytes(f.read())
        c = sqlite3.connect(restored)
        self.assertEqual(c.execute('SELECT COUNT(*) FROM events').fetchone()[0], 200)
        self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        c.close()
        self.assertIsNone(archive.archive_one(self.root, source.name))
        self.assertIn(source.name, json.loads((path.parent/'manifest.json').read_text())['files'])

    def test_open_database_is_never_archived(self):
        source = self.database()
        with open(source, 'rb'):
            with self.assertRaisesRegex(RuntimeError, 'still in use'):
                archive.archive_one(self.root, source.name)
        self.assertTrue(source.exists())

    def test_verification_failure_keeps_original(self):
        source = self.database()
        with patch.object(archive, 'digest_file', return_value=('wrong', 0)):
            with self.assertRaisesRegex(RuntimeError, 'read-back'):
                archive.archive_one(self.root, source.name)
        self.assertTrue(source.exists())
        self.assertFalse(list((self.root/'legacy-archive').glob('*.part')))

    def test_active_data_is_outside_allowlist(self):
        for name in backup.REQUIRED:
            with self.assertRaises(ValueError): archive.archive_one(self.root, name)

    def test_low_space_preserves_source(self):
        source = self.database()
        from collections import namedtuple
        usage = namedtuple('Usage', 'total used free')(100, 99, 1)
        with patch.object(archive.shutil, 'disk_usage', return_value=usage):
            with self.assertRaisesRegex(RuntimeError, 'headroom'):
                archive.archive_one(self.root, source.name)
        self.assertTrue(source.exists())

    def test_mutating_source_is_preserved(self):
        source = self.database()
        before = archive.fingerprint(source)
        with patch.object(archive, 'fingerprint', side_effect=[before, (*before[:3], before[3]+1)]):
            with self.assertRaisesRegex(RuntimeError, 'changed'):
                archive.archive_one(self.root, source.name)
        self.assertTrue(source.exists())

    def test_uninspectable_process_fails_closed(self):
        source = self.database()
        with patch.object(archive.os, 'readlink', side_effect=PermissionError):
            with self.assertRaisesRegex(RuntimeError, 'Cannot verify'):
                REAL_OPEN_HANDLES(source, processes=[Path('/proc/self')])


class RuntimeTests(unittest.TestCase):
    def test_explicit_profile_requires_chart_and_native(self):
        with patch.dict(os.environ, {'LS_LEAN_RUNTIME':'1','LS_NATIVE_CHAIN_V2':'1','LS_CHART_FIRST_V1':'1'}):
            self.assertTrue(profile.enabled())
            with patch.dict(os.environ, {'LS_CHART_FIRST_V1':'0'}): self.assertFalse(profile.enabled())

    def test_chart_workers_include_delivery_measurement_and_no_legacy_loops(self):
        import long_short_daemon as daemon
        daemon.STOP.set()
        try:
            with patch.dict(os.environ, {'LS_FUTURES_HISTORY_RELAY_URL':'http://relay.railway.internal','LS_MAJOR_MOVE_V1':'0'}), \
                 patch.object(daemon.threading, 'Thread') as thread:
                daemon.run_chart_runtime(Path('/tmp'))
                targets = [call.kwargs['target'] for call in thread.call_args_list]
                self.assertEqual(targets.count(daemon.watcher_loop), 7)
                measured = [call.kwargs['args'] for call in thread.call_args_list
                            if call.kwargs['target'] == daemon.watcher_loop]
                self.assertIn(('chart_delivery_outcomes', 'long_short_chart_delivery_outcomes.py'), measured)
                self.assertNotIn(daemon.analyst_loop, targets)
                self.assertNotIn(daemon.research_loop, targets)
                self.assertNotIn(daemon.paper_ledger_loop, targets)
                self.assertEqual(os.environ['LS_EARLY_BREAKOUT_V1_ENABLED'], '0')
        finally: daemon.STOP.clear()

    def test_collectors_no_longer_read_retired_shortlists(self):
        import long_short_futures_kline_shadow as kline
        import long_short_native_derivatives as native
        with patch.object(profile, 'enabled', return_value=True), \
             patch.object(profile, 'demand', return_value=(['NEWUSDT'], ['NEWUSDT'])), \
             patch.object(kline, 'live_watch_symbols', side_effect=AssertionError('retired DB read')):
            self.assertEqual(kline.requested_symbols('/retired.db'), ['NEWUSDT'])
            self.assertEqual(native.demand('/retired.db')[0], ['NEWUSDT'])

    def test_active_and_pending_chart_ideas_precede_rotation(self):
        import long_short_chart_first_worker as worker
        import long_short_analyst as analyst
        with tempfile.TemporaryDirectory() as tmp:
            c = sqlite3.connect(Path(tmp)/'chart_first_v1.db')
            c.execute('CREATE TABLE assessments(symbol TEXT, observed_ms INTEGER, stage TEXT)')
            c.execute('INSERT INTO assessments VALUES(?,?,?)', ('PENDINGUSDT', int(time.time()*1000), 'DELIVERED_READY'))
            c.commit(); c.close()
            with patch.dict(os.environ, {'LS_MAJOR_MOVE_V1':'0'}), \
                 patch.object(worker, 'root', return_value=tmp), \
                 patch.object(worker, 'active_symbols', return_value=['ACTIVEUSDT']), \
                 patch.object(analyst, 'native_futures_websocket_universe', return_value=[('ROTATEUSDT',)]):
                requested, urgent = profile.demand(3)
                self.assertEqual(requested, ['ACTIVEUSDT','PENDINGUSDT','ROTATEUSDT'])
                self.assertEqual(urgent, ['ACTIVEUSDT','PENDINGUSDT'])


class FakeS3:
    def __init__(self): self.objects = {}
    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            from botocore.exceptions import ClientError
            raise ClientError({'Error':{'Code':'404'}}, 'HeadObject')
        blob, metadata = self.objects[Key]
        return {'ContentLength':len(blob), 'Metadata':metadata}
    def upload_file(self, path, bucket, key, ExtraArgs):
        self.objects[key] = (Path(path).read_bytes(), ExtraArgs['Metadata'])
    def put_object(self, Bucket, Key, Body, ContentType): self.objects[Key] = (Body, {})
    def delete_object(self, Bucket, Key): del self.objects[Key]
    def get_object(self, Bucket, Key): return {'Body':io.BytesIO(self.objects[Key][0])}
    def get_paginator(self, _): return self
    def paginate(self, Bucket, Prefix):
        return [{'Contents':[{'Key':k} for k in self.objects if k.startswith(Prefix)]}]


class BackupTests(unittest.TestCase):
    def test_only_the_exact_dated_snapshot_may_be_compacted(self):
        import long_short_remote_archive_compact as remote
        original = b'distinct-old-records'*100
        sha = hashlib.sha256(original).hexdigest()
        s3 = FakeS3()
        s3.objects[remote.DATED_SNAPSHOT] = (original, {'sha256':sha})
        with patch.object(remote, 'DATED_SHA', sha), patch.object(remote, 'DATED_SIZE', len(original)):
            manifest = remote.compact(s3, 'bucket', remote.DATED_SNAPSHOT)
        self.assertEqual(gzip.decompress(s3.objects[manifest['compressed_key']][0]), original)
        self.assertNotIn(remote.DATED_SNAPSHOT, s3.objects)
        with self.assertRaises(ValueError):
            remote.compact(s3, 'bucket', 'archive/v4-native/20261013T140330Z/long_short_v4_native.db')

    def test_dated_snapshot_identity_mismatch_preserves_source(self):
        import long_short_remote_archive_compact as remote
        s3 = FakeS3()
        s3.objects[remote.DATED_SNAPSHOT] = (b'changed', {'sha256':'different'})
        with self.assertRaisesRegex(RuntimeError, 'identity'):
            remote.compact(s3, 'bucket', remote.DATED_SNAPSHOT)
        self.assertIn(remote.DATED_SNAPSHOT, s3.objects)

    def test_remote_archive_verified_before_raw_deletion(self):
        import long_short_remote_archive_compact as remote
        s3 = FakeS3()
        original = b'frozen-v4-records'*100
        sha = hashlib.sha256(original).hexdigest()
        key = 'archive/v4-native/'+sha+'/long_short_v4_native.db'
        s3.objects[key] = (original, {'sha256':sha})
        manifest = remote.compact(s3, 'bucket', key)
        self.assertNotIn(key, s3.objects)
        self.assertEqual(gzip.decompress(s3.objects[manifest['compressed_key']][0]), original)

    def test_bad_remote_replacement_never_deletes_original(self):
        import long_short_remote_archive_compact as remote
        s3 = FakeS3()
        original = b'frozen-v4-records'*100
        sha = hashlib.sha256(original).hexdigest()
        key = 'archive/v4-native/'+sha+'/long_short_v4_native.db'
        s3.objects[key] = (original, {'sha256':sha})
        def corrupt(path, bucket, key, ExtraArgs):
            s3.objects[key] = (gzip.compress(b'wrong'), ExtraArgs['Metadata'])
        with patch.object(s3, 'upload_file', side_effect=corrupt):
            with self.assertRaisesRegex(RuntimeError, 'verification failed'):
                remote.compact(s3, 'bucket', key)
        self.assertEqual(s3.objects[key][0], original)

    def test_compressed_backup_roundtrip_inventory_and_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in backup.REQUIRED:
                c = sqlite3.connect(root/name)
                c.execute('CREATE TABLE test(payload TEXT)')
                c.execute("INSERT INTO test VALUES('preserved')")
                c.commit(); c.close()
            legacy = root/'shortlist_audit.jsonl'
            legacy.write_text('{"reason":"old"}\n'*100)
            with patch.object(archive, 'open_handles', side_effect=own_process_handles):
                archive.archive_one(root, legacy.name)
            s3 = FakeS3()
            backup.run(s3, 'bucket', root)
            key = next(k for k in s3.objects if k.startswith('daily/') and k.endswith('/manifest.json'))
            manifest = json.loads(s3.objects[key][0])
            self.assertTrue(backup.valid_manifest(s3, 'bucket', key, manifest))
            self.assertEqual(len(manifest['archives']), 2)
            n = len(s3.objects)
            backup.run(s3, 'bucket', root)
            self.assertEqual(len(s3.objects), n)
            # A verified old snapshot must not hide a newly introduced motor DB.
            extra=sqlite3.connect(root/'major_move_v1.db')
            extra.execute('CREATE TABLE evidence(id INTEGER)');extra.commit();extra.close()
            backup.run(s3, 'bucket', root)
            inventories=[json.loads(v[0]) for k,v in s3.objects.items() if k.startswith('daily/') and k.endswith('/manifest.json')]
            self.assertTrue(any('major_move_v1.db' in m['expected_sources'] for m in inventories))
            first = manifest['databases'][0]
            restored = root/'restore.db'
            restored.write_bytes(gzip.decompress(s3.objects[first['key']][0]))
            c = sqlite3.connect(restored)
            self.assertEqual(c.execute('SELECT payload FROM test').fetchone()[0], 'preserved')
            c.close()
            broken = dict(manifest, databases=manifest['databases'][:-1])
            self.assertFalse(backup.valid_manifest(s3, 'bucket', key, broken))
            blob, meta = s3.objects[first['key']]
            s3.objects[first['key']] = (blob[:-10], meta)
            self.assertFalse(backup.valid_manifest(s3, 'bucket', key, manifest))

    def test_missing_core_does_not_publish_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            s3 = FakeS3()
            with self.assertRaisesRegex(RuntimeError, 'incomplete'):
                backup.run(s3, 'bucket', Path(tmp))
            self.assertFalse(s3.objects)


if __name__ == '__main__': unittest.main()
