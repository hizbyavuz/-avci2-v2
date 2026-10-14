#!/usr/bin/env python3
"""One-time conservative reclaim of optional V4 native research DB after S3 validation."""
import json, os, shutil, sqlite3
from pathlib import Path
import boto3
from botocore.config import Config

TARGET="long_short_v4_native.db"
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
    with sqlite3.connect(f"file:{path}?mode=ro",uri=True) as db:
        if db.execute("PRAGMA quick_check").fetchone()[0]!="ok":raise RuntimeError("V4 local integrity failed")
    client=boto3.client("s3",endpoint_url=os.environ["LS_BACKUP_ENDPOINT"],
        aws_access_key_id=os.environ["LS_BACKUP_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["LS_BACKUP_SECRET_ACCESS_KEY"],
        region_name=os.getenv("LS_BACKUP_REGION","auto"),
        config=Config(s3={"addressing_style":"virtual"}))
    bucket=os.environ["LS_BACKUP_BUCKET"]
    manifests=[]
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket,Prefix="daily/"):
        manifests.extend(o["Key"] for o in page.get("Contents",[]) if o["Key"].endswith("/manifest.json"))
    verified=None
    for key in sorted(manifests,reverse=True)[:8]:
        manifest=json.loads(client.get_object(Bucket=bucket,Key=key)["Body"].read())
        if len(manifest.get("databases",[]))<15:continue
        entry=next((d for d in manifest["databases"] if d["name"]==TARGET),None)
        if not entry:continue
        head=client.head_object(Bucket=bucket,Key=entry["key"])
        if int(head["ContentLength"])!=int(entry["bytes"]):continue
        if head.get("Metadata",{}).get("sha256")!=entry["sha256"]:continue
        verified={"manifest":key,"object":entry["key"],"bytes":entry["bytes"]}
        break
    if not verified:raise RuntimeError("No validated V4 backup; refuse reclaim")
    # Never remove active sidecars: a WAL could contain uncheckpointed changes.
    for suffix in ("-wal","-shm","-journal"):
        if (root/(TARGET+suffix)).exists():raise RuntimeError("V4 SQLite sidecar exists; refuse reclaim")
    before=shutil.disk_usage(root).free
    path.unlink()
    after=shutil.disk_usage(root).free
    print("LS_V4_RECLAIM_OK "+json.dumps({"backup":verified,"freed_bytes":after-before,
        "free_bytes_after":after}),flush=True)

if __name__=="__main__":main()
