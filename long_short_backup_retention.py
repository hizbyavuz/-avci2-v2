#!/usr/bin/env python3
"""Keep at most 24 hours of redundant S3 backups after validating a complete set.

Never touches local signal databases. Fail closed if newest backup is incomplete.
"""
import datetime
import json
import os
import boto3
from botocore.config import Config

def main():
    client=boto3.client("s3",endpoint_url=os.environ["LS_BACKUP_ENDPOINT"],
        aws_access_key_id=os.environ["LS_BACKUP_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["LS_BACKUP_SECRET_ACCESS_KEY"],
        region_name=os.getenv("LS_BACKUP_REGION","auto"),
        config=Config(s3={"addressing_style":"virtual"}))
    bucket=os.environ["LS_BACKUP_BUCKET"]
    objects=[]
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket,Prefix="daily/"):
        objects.extend(page.get("Contents",[]))
    manifests=sorted((o["Key"] for o in objects if o["Key"].endswith("/manifest.json")),reverse=True)
    if not manifests:
        raise RuntimeError("No completed backup manifests: refuse cleanup")
    selected=None
    for key in manifests[:8]:
        manifest=json.loads(client.get_object(Bucket=bucket,Key=key)["Body"].read())
        databases=manifest.get("databases",[])
        if len(databases)<15:
            continue
        prefix=key.rsplit("/",1)[0]+"/"
        if any(not d.get("key","").startswith(prefix) for d in databases):
            continue
        try:
            for d in databases:
                head=client.head_object(Bucket=bucket,Key=d["key"])
                if int(head["ContentLength"])!=int(d["bytes"]) or head.get("Metadata",{}).get("sha256")!=d["sha256"]:
                    raise ValueError("object integrity metadata mismatch")
        except Exception:
            continue
        selected=(key,manifest)
        break
    if not selected:
        raise RuntimeError("No validated complete backup: refuse cleanup")
    key,manifest=selected
    cutoff=datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(hours=24)
    protected={key}|{d["key"] for d in manifest["databases"]}
    expired=[{"Key":o["Key"]} for o in objects if o["LastModified"]<cutoff and o["Key"] not in protected]
    count=0
    for i in range(0,len(expired),1000):
        result=client.delete_objects(Bucket=bucket,Delete={"Objects":expired[i:i+1000],"Quiet":True})
        if result.get("Errors"):
            raise RuntimeError("S3 delete reported errors")
        count+=len(expired[i:i+1000])
    print("LS_BACKUP_RETENTION "+json.dumps({"protected_manifest":key,
        "verified_databases":len(manifest["databases"]),"deleted_objects":count,
        "cutoff_utc":cutoff.isoformat()}),flush=True)

if __name__=="__main__":
    main()
