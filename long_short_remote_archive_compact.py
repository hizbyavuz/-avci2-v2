"""Losslessly compress the already-retired raw V4 object-storage archives.

Only exact archive/v4-native/<sha256>/long_short_v4_native.db keys qualify.
Read back and decompress the replacement before deleting redundant raw bytes.
"""
import gzip
import hashlib
import json
import os
import re
import shutil
import tempfile
from contextlib import closing
from pathlib import Path

from long_short_legacy_archive import digest_file, CHUNK

PATTERN = re.compile(r'^archive/v4-native/([0-9a-f]{64})/long_short_v4_native\.db$')


def compact(client, bucket, key):
    match = PATTERN.fullmatch(key)
    if not match: raise ValueError('Not an approved retired V4 archive')
    expected = match.group(1)
    head = client.head_object(Bucket=bucket, Key=key)
    if head.get('Metadata', {}).get('sha256') != expected:
        raise RuntimeError('Original archive identity is not verified')
    size = int(head['ContentLength'])
    with tempfile.TemporaryDirectory(prefix='ls-v4-compact-') as tmp:
        path = Path(tmp)/'long_short_v4_native.db.gz'
        h, count = hashlib.sha256(), 0
        with closing(client.get_object(Bucket=bucket, Key=key)['Body']) as src, gzip.open(path, 'wb', compresslevel=6) as dst:
            for block in iter(lambda: src.read(CHUNK), b''):
                if shutil.disk_usage(tmp).free < 128*CHUNK:
                    raise RuntimeError('Low temporary disk space; original retained')
                h.update(block); count += len(block); dst.write(block)
        if (h.hexdigest(), count) != (expected, size):
            raise RuntimeError('Original archive content hash mismatch')
        compressed_sha, compressed_size = digest_file(path)
        new_key = key+'.gz'
        client.upload_file(str(path), bucket, new_key,
            ExtraArgs={'Metadata': {'sha256':compressed_sha, 'source-sha256':expected}})
        h, count = hashlib.sha256(), 0
        with closing(client.get_object(Bucket=bucket, Key=new_key)['Body']) as raw:
            with gzip.GzipFile(fileobj=raw, mode='rb') as restored:
                for block in iter(lambda: restored.read(CHUNK), b''):
                    h.update(block); count += len(block)
        if (h.hexdigest(), count) != (expected, size):
            raise RuntimeError('Remote decompression verification failed; original retained')
        manifest = dict(schema=1, original_key=key, original_sha256=expected,
            original_bytes=size, compressed_key=new_key, compressed_sha256=compressed_sha,
            compressed_bytes=compressed_size, restore='gzip decompression; SHA256 must equal original_sha256')
        manifest_key = key.rsplit('/', 1)[0]+'/manifest.json'
        client.put_object(Bucket=bucket, Key=manifest_key,
            Body=json.dumps(manifest, sort_keys=True).encode(), ContentType='application/json')
        with closing(client.get_object(Bucket=bucket, Key=manifest_key)['Body']) as body:
            if json.loads(body.read()) != manifest:
                raise RuntimeError('Recovery manifest verification failed')
        current = client.head_object(Bucket=bucket, Key=key)
        if (current['ContentLength'] != size or current.get('Metadata', {}).get('sha256') != expected
                or current.get('ETag') != head.get('ETag')):
            raise RuntimeError('Original changed during compaction')
        client.delete_object(Bucket=bucket, Key=key)
        print('LS_REMOTE_ARCHIVE_COMPACT_OK '+json.dumps(manifest), flush=True)
        return manifest


def main():
    import boto3
    from botocore.config import Config
    from long_short_runtime_profile import enabled
    if not enabled(): raise RuntimeError('Chart-only runtime required')
    client=boto3.client('s3', endpoint_url=os.environ['LS_BACKUP_ENDPOINT'],
        aws_access_key_id=os.environ['LS_BACKUP_ACCESS_KEY_ID'],
        aws_secret_access_key=os.environ['LS_BACKUP_SECRET_ACCESS_KEY'],
        region_name=os.getenv('LS_BACKUP_REGION','auto'),
        config=Config(s3={'addressing_style':'virtual'}))
    bucket=os.environ['LS_BACKUP_BUCKET']
    keys=[]
    for page in client.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix='archive/v4-native/'):
        keys.extend(o['Key'] for o in page.get('Contents', []) if PATTERN.fullmatch(o['Key']))
    print('LS_REMOTE_ARCHIVE_COMPACT_START '+json.dumps({'raw_archives':len(keys)}), flush=True)
    for key in keys: compact(client, bucket, key)


if __name__ == '__main__': main()
