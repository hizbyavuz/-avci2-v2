#!/usr/bin/env python3
"""Read-only S3 backup inventory. Does not expose credentials or delete objects."""
import json
import os
from collections import defaultdict
import boto3
from botocore.config import Config

def main():
    bucket=os.environ["LS_BACKUP_BUCKET"]
    client=boto3.client("s3",endpoint_url=os.environ["LS_BACKUP_ENDPOINT"],
        aws_access_key_id=os.environ["LS_BACKUP_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["LS_BACKUP_SECRET_ACCESS_KEY"],
        region_name=os.getenv("LS_BACKUP_REGION","auto"),
        config=Config(s3={"addressing_style":"virtual"}))
    count=total=0
    by_day=defaultdict(lambda:{"objects":0,"bytes":0})
    manifests=[]
    non_daily=[]
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        for obj in page.get("Contents",[]):
            key=obj["Key"]
            size=obj["Size"]
            count+=1
            total+=size
            parts=key.split("/")
            day=parts[1] if len(parts)>2 and parts[0]=="daily" else "other"
            by_day[day]["objects"]+=1
            by_day[day]["bytes"]+=size
            if day=='other' and len(non_daily)<50:
                item={'key':key,'bytes':size}
                if size>=1_000_000_000:
                    head=client.head_object(Bucket=bucket,Key=key)
                    item['sha256']=head.get('Metadata',{}).get('sha256')
                non_daily.append(item)
            if key.endswith("/manifest.json"):
                manifests.append(key)
    print("LS_BACKUP_INVENTORY "+json.dumps({
        "objects":count,"bytes":total,"days":dict(sorted(by_day.items())),
        "manifest_count":len(manifests),"latest_manifests":sorted(manifests)[-5:],
        "non_daily_objects":non_daily},
        separators=(",",":")),flush=True)

if __name__=="__main__":
    main()
