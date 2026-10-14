#!/usr/bin/env python3
"""One-time conservative reclaim of optional V4 native research DB after S3 validation."""
import json, os, shutil, sqlite3, hashlib
from contextlib import closing
from pathlib import Path
import boto3
from botocore.config import Config

TARGET="long_short_v4_native.db"
def close_idle_database(path):
    # A sqlite Connection context manager commits, but does NOT close it.
    # Checkpoint through SQLite, never unlink WAL files by hand.
    with closing(sqlite3.connect(path, timeout=5)) as db:
        busy, _, _ = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if busy:
            raise RuntimeError("V4 checkpoint busy; refuse reclaim")
        if db.execute("PRAGMA journal_mode=DELETE").fetchone()[0].lower() != "delete":
            raise RuntimeError("V4 still in WAL mode; refuse reclaim")
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("V4 local integrity failed")


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    root=Path(os.getenv("LS_STATE_DIR") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or ".long-short-state").resolve()
    path=root/TARGET
    if not path.is_file():
        print("LS_V4_RECLAIM_SKIPPED missing",flush=True);return
    if not str(root).startswith("/app/.long-short-state"):
        raise RuntimeError("unexpected state directory")
    usage=shutil.disk_usage(root)
    if usage.used/usage.total<.85:
        print("LS_V4_RECLAIM_SKIPPED pressure_below_threshold",flush=True);return
    if path.stat().st_size<1_000_000_000:
        raise RuntimeError("unexpected V4 database size")
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():continue
        try:
            for fd in (proc/"fd").iterdir():
                try:
                    if str(path) in os.readlink(fd):raise RuntimeError("V4 database currently open")
                except OSError:pass
        except (OSError,PermissionError):continue
    close_idle_database(path)
    client=boto3.client("s3",endpoint_url=os.environ["LS_BACKUP_ENDPOINT"],
        aws_access_key_id=os.environ["LS_BACKUP_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["LS_BACKUP_SECRET_ACCESS_KEY"],
        region_name=os.getenv("LS_BACKUP_REGION","auto"),
        config=Config(s3={"addressing_style":"virtual"}))
    bucket=os.environ["LS_BACKUP_BUCKET"]
    # Archive the CURRENT checkpointed bytes, not a potentially older daily copy.
    digest=file_hash(path)
    original=path.stat()
    archive_key="archive/v4-native/"+digest+"/"+TARGET
    try:
        head=client.head_object(Bucket=bucket,Key=archive_key)
    except client.exceptions.ClientError as exc:
        if str(exc.response.get("Error",{}).get("Code")) not in ("404","NoSuchKey","NotFound"):
            raise
        head=None
    if not head or int(head["ContentLength"])!=original.st_size or head.get("Metadata",{}).get("sha256")!=digest:
        client.upload_file(str(path),bucket,archive_key,ExtraArgs={"Metadata":{"sha256":digest}})
    # Read back the content: SHA metadata alone is not end-to-end verification.
    remote=client.get_object(Bucket=bucket,Key=archive_key)
    remote_digest=hashlib.sha256()
    with closing(remote["Body"]) as body:
        for chunk in iter(lambda:body.read(1024*1024),b""):
            remote_digest.update(chunk)
    if remote_digest.hexdigest()!=digest:
        raise RuntimeError("V4 archive content verification failed")
    current=path.stat()
    if (current.st_size,current.st_mtime_ns)!=(original.st_size,original.st_mtime_ns):
        raise RuntimeError("V4 changed during archive; refuse reclaim")
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():continue
        try:
            for fd in (proc/"fd").iterdir():
                try:
                    if str(path) in os.readlink(fd):raise RuntimeError("V4 reopened; refuse reclaim")
                except OSError:pass
        except (OSError,PermissionError):continue
    for suffix in ("-wal","-shm","-journal"):
        if (root/(TARGET+suffix)).exists():raise RuntimeError("V4 sidecar reappeared; refuse reclaim")
    verified={"permanent_archive":archive_key,"sha256":digest,"bytes":original.st_size,
              "current_content_verified":True}
    before=shutil.disk_usage(root).free
    path.unlink()
    after=shutil.disk_usage(root).free
    print("LS_V4_RECLAIM_OK "+json.dumps({"backup":verified,"freed_bytes":after-before,
        "free_bytes_after":after}),flush=True)

if __name__=="__main__":main()
