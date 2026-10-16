"""Compressed daily snapshots plus content-addressed, reusable legacy archives."""
import datetime
import gzip
import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

from long_short_legacy_archive import digest_file

REQUIRED = {'chart_first_v1.db', 'binance_futures_kline_shadow.db',
            'binance_futures_universe.db', 'native_derivatives_v2.db',
            'long_short_taker_minute_shadow.db', 'long_short_message_cards.db'}


def verified(client, bucket, row):
    head = client.head_object(Bucket=bucket, Key=row['key'])
    return (int(head['ContentLength']) == int(row['bytes']) and
            head.get('Metadata', {}).get('sha256') == row['sha256'])


def valid_manifest(client, bucket, key, manifest):
    """Exact inventory + core files replace the obsolete '15 databases' rule."""
    if manifest.get('schema') != 2: return False
    files = manifest.get('databases', [])
    names = [r.get('name') for r in files]
    expected = manifest.get('expected_sources', [])
    if (len(names) != len(set(names)) or set(names) != set(expected)
            or not REQUIRED.issubset(names)):
        return False
    prefix = key.rsplit('/', 1)[0]+'/'
    if any(not r.get('key', '').startswith(prefix) for r in files): return False
    archives = manifest.get('archives', [])
    if any(not r.get('key', '').startswith('archives/legacy/') for r in archives): return False
    return all(verified(client, bucket, r) for r in files+archives)


def upload(client, bucket, path, key, reuse=False):
    digest, size = digest_file(path)
    row = dict(name=path.name, key=key, sha256=digest, bytes=size)
    exists = False
    if reuse:
        try: exists = verified(client, bucket, row)
        except Exception as exc:
            # Only absence is permission to upload; auth/transport failures propagate.
            code = getattr(exc, 'response', {}).get('Error', {}).get('Code')
            if code not in ('404', 'NoSuchKey', 'NotFound'): raise
    if not exists:
        client.upload_file(str(path), bucket, key, ExtraArgs={'Metadata': {'sha256': digest}})
    if not verified(client, bucket, row): raise RuntimeError('Backup verification failed: '+path.name)
    return row


def run(client, bucket, state):
    now = datetime.datetime.now(datetime.timezone.utc)
    day, stamp = now.strftime('%Y-%m-%d'), now.strftime('%Y%m%dT%H%M%SZ')
    prefix = f'daily/{day}/{stamp}/'
    sources = sorted(set(state.glob('*.db')) | set(state.glob('*.sqlite')))
    expected = [p.name for p in sources]
    # Only a verified schema-2 set counts as today's compact backup.
    for page in client.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix=f'daily/{day}/'):
        for item in page.get('Contents', []):
            key = item['Key']
            if not key.endswith('/manifest.json'): continue
            m = json.loads(client.get_object(Bucket=bucket, Key=key)['Body'].read())
            if set(m.get('expected_sources',[]))==set(expected) and valid_manifest(client, bucket, key, m):
                print('LS_BACKUP_COMPACT_ALREADY_PRESENT '+key, flush=True)
                return
    if not REQUIRED.issubset(expected): raise RuntimeError('Core state is incomplete; keep old backup')
    archives = []
    archive_dir = state/'legacy-archive'
    manifest_path = archive_dir/'manifest.json'
    if manifest_path.is_file():
        m = json.loads(manifest_path.read_text())
        for row in m['files'].values():
            path = archive_dir/row['archive']
            if path.name != row['archive'] or path.parent != archive_dir:
                raise ValueError('Invalid archive path')
            if digest_file(path) != (row['archive_sha256'], row['archive_bytes']):
                raise RuntimeError('Local legacy archive differs from retirement manifest')
            key = 'archives/legacy/'+row['archive_sha256']+'/'+path.name
            uploaded = upload(client, bucket, path, key, reuse=True)
            uploaded.update(original_name=row['name'], source_sha256=row['source_sha256'],
                            source_bytes=row['source_bytes'])
            archives.append(uploaded)
        archives.append(upload(client, bucket, manifest_path,
            'archives/legacy/'+digest_file(manifest_path)[0]+'/manifest.json', reuse=True))
    results = []
    for source in sources:
        with tempfile.TemporaryDirectory(prefix='ls-compact-backup-') as tmp:
            dest = Path(tmp)/source.name
            src = sqlite3.connect(f'file:{source.resolve()}?mode=ro', uri=True, timeout=30)
            dst = sqlite3.connect(dest)
            try:
                src.backup(dst, pages=200, sleep=.1)
                if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('Snapshot integrity failed: '+source.name)
            finally:
                dst.close()
                src.close()
            original_digest, original_size = digest_file(dest)
            compressed = dest.with_name(dest.name+'.gz')
            with open(dest, 'rb') as inp, gzip.open(compressed, 'wb', compresslevel=6) as out:
                shutil.copyfileobj(inp, out, 1024*1024)
            if digest_file(compressed, compressed=True) != (original_digest, original_size):
                raise RuntimeError('Snapshot compression verification failed')
            row = upload(client, bucket, compressed, prefix+compressed.name)
            row.update(name=source.name, source_sha256=original_digest, source_bytes=original_size)
            results.append(row)
    manifest = dict(schema=2, timestamp=stamp, expected_sources=expected,
        databases=results, archives=archives, commit=os.getenv('RAILWAY_GIT_COMMIT_SHA', 'UNAVAILABLE'))
    key = prefix+'manifest.json'
    if not valid_manifest(client, bucket, key, manifest): raise RuntimeError('Incomplete new backup')
    client.put_object(Bucket=bucket, Key=key, Body=json.dumps(manifest, sort_keys=True).encode(),
                      ContentType='application/json')
    print('LS_BACKUP_COMPACT_OK '+json.dumps(dict(count=len(results), archives=len(archives),
        daily_bytes=sum(r['bytes'] for r in results), archive_bytes=sum(r['bytes'] for r in archives),
        manifest=key)), flush=True)
