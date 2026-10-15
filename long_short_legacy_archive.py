"""Lossless retirement of explicit legacy files, never active chart/source DBs.

Checkpoint SQLite with all writers stopped, check integrity, gzip, decompress
and hash-verify, durably publish a manifest, then unlink the redundant original.
An open handle, mutation, low disk space, or failed verification keeps the source.
"""
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import time
from pathlib import Path

LEGACY_FILES = (
    'long_short_breakout_observer.db', 'long_short_analyst.db',
    'long_short_analyst_v2.db', 'long_short_live_pool_v2.db',
    'long_short_live_pool.db', 'data_opportunity_v2.db',
    'long_short_research.db', 'long_short_reversal_live.db',
    'full_universe_forward.sqlite', 'early_forward_outcomes.sqlite',
    'full_universe_research.jsonl', 'broad_early_observations.jsonl',
    'shortlist_audit.jsonl', 'signal_gate_audit.jsonl', 'coinalyze_cache.db',
)
CHUNK = 1024*1024


def digest_file(path, compressed=False):
    h = hashlib.sha256()
    size = 0
    opener = gzip.open if compressed else open
    with opener(path, 'rb') as f:
        for block in iter(lambda: f.read(CHUNK), b''):
            h.update(block)
            size += len(block)
    return h.hexdigest(), size


def durable_json(path, value):
    tmp = path.with_name(path.name+'.tmp')
    with open(tmp, 'w') as f:
        json.dump(value, f, ensure_ascii=False, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    fd = os.open(path.parent, os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def open_handles(path, processes=None):
    targets = {str(path.resolve())+suffix for suffix in ('', '-wal', '-shm')}
    found = []
    for proc in (Path('/proc').glob('[0-9]*') if processes is None else processes):
        try:
            for fd in (proc/'fd').iterdir():
                try:
                    if os.readlink(fd) in targets: found.append(str(fd))
                except FileNotFoundError: pass
        except FileNotFoundError: pass
        except PermissionError:
            raise RuntimeError('Cannot verify all process handles')
    return found


def fingerprint(path):
    s = path.stat()
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns


def archive_one(root, name):
    if name not in LEGACY_FILES:
        raise ValueError('File is not on the retirement allowlist')
    source = root/name
    if not source.exists(): return None
    if source.is_symlink() or not source.is_file(): raise ValueError('Not a regular legacy file')
    if open_handles(source): raise RuntimeError('Legacy file still in use')
    if source.suffix in ('.db', '.sqlite'):
        c = sqlite3.connect(f'file:{source.resolve()}?mode=rw', uri=True, timeout=5)
        try:
            checkpoint = c.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
            if checkpoint[0]: raise RuntimeError('SQLite checkpoint busy')
            if c.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise RuntimeError('SQLite integrity check failed')
        finally: c.close()
    before = fingerprint(source)
    archive = root/'legacy-archive'
    archive.mkdir(exist_ok=True)
    manifest_path = archive/'manifest.json'
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {
        'schema': 1, 'policy': 'lossless-retirement', 'files': {},
        'pending_legacy_outcomes': 'Frozen as recorded; no fabricated completion. Current chart outcomes stay active.'}
    tmp = archive/(name+'.part')
    h = hashlib.sha256()
    try:
        with open(source, 'rb') as src, open(tmp, 'wb') as raw:
            with gzip.GzipFile(filename='', fileobj=raw, mode='wb', compresslevel=6, mtime=0) as dst:
                for block in iter(lambda: src.read(CHUNK), b''):
                    if shutil.disk_usage(root).free < 64*CHUNK:
                        raise RuntimeError('Insufficient headroom; source preserved')
                    h.update(block)
                    dst.write(block)
            raw.flush()
            os.fsync(raw.fileno())
        sha = h.hexdigest()
        if digest_file(tmp, compressed=True) != (sha, before[2]):
            raise RuntimeError('Archive read-back mismatch')
        if fingerprint(source) != before or open_handles(source):
            raise RuntimeError('Legacy file changed or reopened during archiving')
        dest = archive/(name+'.'+sha[:16]+'.gz')
        os.replace(tmp, dest)
        row = dict(name=name, archive=dest.name, source_sha256=sha,
                   source_bytes=before[2], archive_bytes=dest.stat().st_size,
                   archive_sha256=digest_file(dest)[0], archived_ms=int(time.time()*1000))
        manifest['files'][name] = row
        durable_json(manifest_path, manifest)
        # Recheck after publishing recovery metadata, before removing redundancy.
        if fingerprint(source) != before or open_handles(source):
            raise RuntimeError('Legacy source reopened; original preserved')
        source.unlink()
        print('LS_LEGACY_ARCHIVE_FILE '+json.dumps(row), flush=True)
        return row
    finally:
        tmp.unlink(missing_ok=True)


def main():
    from long_short_runtime_profile import enabled
    if not enabled(): raise RuntimeError('Retirement requires the chart-only runtime')
    root = Path(os.getenv('LS_STATE_DIR') or os.getenv('RAILWAY_VOLUME_MOUNT_PATH') or '.long-short-state')
    before = shutil.disk_usage(root)
    rows, errors = [], []
    for name in LEGACY_FILES:
        try:
            row = archive_one(root, name)
            if row: rows.append(row)
        except Exception as exc:
            errors.append(dict(name=name, error=str(exc)[:180]))
            print('LS_LEGACY_ARCHIVE_ERROR '+json.dumps(errors[-1]), flush=True)
    after = shutil.disk_usage(root)
    print('LS_LEGACY_ARCHIVE_SUMMARY '+json.dumps(dict(files=len(rows),
        source_bytes=sum(r['source_bytes'] for r in rows),
        archive_bytes=sum(r['archive_bytes'] for r in rows),
        disk_used_before=before.used, disk_used_after=after.used,
        disk_total_bytes=after.total, disk_free_bytes=after.free, errors=errors)), flush=True)
    return int(bool(errors))


if __name__ == '__main__': raise SystemExit(main())
