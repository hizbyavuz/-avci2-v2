#!/usr/bin/env python3
"""Consistent daily SQLite snapshots to Railway S3-compatible object storage."""
import datetime, hashlib, json, os, sqlite3, tempfile
from pathlib import Path
import boto3
from botocore.config import Config

def main():
    state=Path(os.getenv("LS_STATE_DIR") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or ".long-short-state")
    bucket=os.environ["LS_BACKUP_BUCKET"]
    client=boto3.client("s3",endpoint_url=os.environ["LS_BACKUP_ENDPOINT"],aws_access_key_id=os.environ["LS_BACKUP_ACCESS_KEY_ID"],aws_secret_access_key=os.environ["LS_BACKUP_SECRET_ACCESS_KEY"],region_name=os.getenv("LS_BACKUP_REGION","auto"),config=Config(s3={"addressing_style":"virtual"}))
    day=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    timestamp=datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    results=[]
    for source in sorted(state.glob("*.db")):
        with tempfile.TemporaryDirectory() as tmp:
            dest=Path(tmp)/source.name
            with sqlite3.connect(f"file:{source}?mode=ro",uri=True,timeout=30) as src:
                with sqlite3.connect(dest) as dst: src.backup(dst,pages=200,sleep=.1)
            with sqlite3.connect(dest) as check:
                if check.execute("PRAGMA integrity_check").fetchone()[0]!="ok": raise RuntimeError("integrity failure: "+source.name)
            digest=hashlib.sha256(dest.read_bytes()).hexdigest()
            key=f"daily/{day}/{timestamp}/{source.name}"
            client.upload_file(str(dest),bucket,key,ExtraArgs={"Metadata":{"sha256":digest}})
            head=client.head_object(Bucket=bucket,Key=key)
            if head.get("Metadata",{}).get("sha256")!=digest: raise RuntimeError("upload verification failed: "+key)
            results.append({"name":source.name,"key":key,"sha256":digest,"bytes":dest.stat().st_size})
    manifest=json.dumps({"timestamp":timestamp,"databases":results,"commit":os.getenv("RAILWAY_GIT_COMMIT_SHA","UNAVAILABLE")},sort_keys=True).encode()
    client.put_object(Bucket=bucket,Key=f"daily/{day}/{timestamp}/manifest.json",Body=manifest,ContentType="application/json")
    print("LS_BACKUP_OK "+json.dumps({"count":len(results),"timestamp":timestamp,"bucket":bucket}),flush=True)
    if not results: raise RuntimeError("no SQLite databases found")
if __name__=="__main__":main()
