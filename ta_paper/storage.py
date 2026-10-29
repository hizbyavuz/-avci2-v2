"""Lossless, allowlisted archival of retired replay outputs; no live evidence pruning."""
from contextlib import closing
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time


# Exact directories observed in the production inventory on 2026-10-29.
# Each output keeps its own archive and identity, even if sizes are equal.
REPLAY_RUNS = (
    '0f009955b6dfa551', '2bd3c991735a70b5', 'c8866b4e500e6755',
    '0f547f1f8543a873', '49f2595b6d420030', '8d7881ca7a92bec0',
    'f66d9380a5cb83be', '1cc973eda2a4363f',
)
RESERVE_BYTES = 32 * 1024 * 1024
CHUNK = 1024 * 1024


def log(kind, **fields):
    print(kind + ' ' + json.dumps(fields, sort_keys=True), flush=True)


def digest(stream):
    sha = hashlib.sha256()
    size = 0
    for data in iter(lambda: stream.read(CHUNK), b''):
        sha.update(data)
        size += len(data)
    return sha.hexdigest(), size


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def record(folder, event):
    path = folder / 'storage_archive_20261029.jsonl'
    if path.is_symlink():
        raise ValueError('SYMLINK_MANIFEST')
    with path.open('a') as out:
        out.write(json.dumps(dict(ms=int(time.time()*1000), **event), sort_keys=True)+'\n')
        out.flush()
        os.fsync(out.fileno())
    sync_dir(folder)


def archive_one(folder, run):
    if run not in REPLAY_RUNS:
        raise ValueError('NOT_ALLOWLISTED')
    directory = folder / run
    path = directory / 'replay_results.sqlite3'
    if directory.is_symlink() or path.is_symlink():
        raise ValueError('SYMLINK_SOURCE')
    if not path.exists():
        return dict(run=run, status='ABSENT_OR_ALREADY_ARCHIVED')
    state = directory / 'job_status.json'
    if state.is_symlink() or not state.exists():
        raise ValueError('MISSING_JOB_STATUS')
    job_status = json.loads(state.read_text()).get('status')
    if job_status not in ('COMPLETE', 'COMPLETE_WITH_GAPS', 'FAILED'):
        raise ValueError('JOB_NOT_TERMINAL')
    # Replay uses the default rollback journal. Never drop a WAL or journal.
    for suffix in ('-wal', '-journal', '-shm'):
        sidecar = Path(str(path)+suffix)
        if sidecar.is_symlink() or (sidecar.exists() and sidecar.stat().st_size):
            raise ValueError('SQLITE_SIDECAR_PRESENT')
    if shutil.disk_usage(folder).free <= RESERVE_BYTES:
        raise ValueError('INSUFFICIENT_FREE_SPACE')

    with closing(sqlite3.connect(path.as_uri()+'?mode=rw', uri=True, timeout=0)) as db:
        if db.execute('PRAGMA journal_mode').fetchone()[0].lower() != 'delete':
            raise ValueError('UNEXPECTED_JOURNAL_MODE')
        db.execute('BEGIN EXCLUSIVE')
        if db.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise ValueError('SQLITE_INTEGRITY_FAILED')
        rows = db.execute('SELECT COUNT(*) FROM outcomes').fetchone()[0]
        before = path.stat()
        with path.open('rb') as src:
            original_hash, original_size = digest(src)
        archive = directory / ('replay_results.sqlite3.'+original_hash+'.gz')
        if archive.is_symlink():
            raise ValueError('SYMLINK_ARCHIVE')
        if not archive.exists():
            temporary = archive.with_suffix('.gz.tmp')
            # A prior interrupted attempt is evidence too; do not overwrite it.
            with temporary.open('xb') as raw:
                try:
                    with gzip.GzipFile(filename='', fileobj=raw, mode='wb', compresslevel=6, mtime=0) as zipped:
                        with path.open('rb') as src:
                            for data in iter(lambda: src.read(CHUNK), b''):
                                if shutil.disk_usage(folder).free < RESERVE_BYTES + CHUNK:
                                    raise ValueError('FREE_SPACE_RESERVE_REACHED')
                                zipped.write(data)
                    raw.flush()
                    os.fsync(raw.fileno())
                except BaseException:
                    # Only this attempt's incomplete temporary file is removed.
                    temporary.unlink(missing_ok=True)
                    raise
            with gzip.open(temporary, 'rb') as restored:
                if digest(restored) != (original_hash, original_size):
                    raise ValueError('ARCHIVE_ROUNDTRIP_MISMATCH')
            temporary.replace(archive)
            sync_dir(directory)
        with gzip.open(archive, 'rb') as restored:
            if digest(restored) != (original_hash, original_size):
                raise ValueError('ARCHIVE_ROUNDTRIP_MISMATCH')
        with path.open('rb') as src:
            if digest(src) != (original_hash, original_size):
                raise ValueError('SOURCE_CHANGED')
        after = path.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError('SOURCE_CHANGED')
        archived_size = archive.stat().st_size
        if archived_size >= original_size:
            return dict(run=run, status='NO_SAVINGS_RAW_RETAINED')
        evidence = dict(run=run, source=str(path.relative_to(folder)),
                        archive=str(archive.relative_to(folder)), sha256=original_hash,
                        original_bytes=original_size, archive_bytes=archived_size,
                        outcome_rows=rows, job_status=job_status)
        record(folder, dict(status='VERIFIED_ARCHIVE_RAW_MAY_EXIST', **evidence))
        # The job lock and exclusive DB transaction are held through removal.
        # No DB content was changed; rollback/close only releases the lock.
        path.unlink()
        sync_dir(directory)
        db.rollback()
    record(folder, dict(status='ARCHIVED', **evidence))
    return dict(status='ARCHIVED', freed_bytes=original_size-archived_size, **evidence)


def archive_retired_replays(folder):
    folder = Path(folder).absolute()
    if not folder.exists():
        return
    if folder.is_symlink() or folder.resolve() != folder:
        raise ValueError('SYMLINK_RESEARCH_DIRECTORY')
    lock_path = folder / 'job.lock'
    if lock_path.is_symlink():
        raise ValueError('SYMLINK_JOB_LOCK')
    before = shutil.disk_usage(folder).free
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for run in REPLAY_RUNS:
            try:
                log('TA_STORAGE_ARCHIVE', **archive_one(folder, run))
            except Exception as exc:
                log('TA_STORAGE_ARCHIVE', run=run, status='RAW_RETAINED', reason=str(exc))
    log('TA_STORAGE_COMPLETE', before_free_bytes=before,
        after_free_bytes=shutil.disk_usage(folder).free,
        live_databases_untouched=True)
