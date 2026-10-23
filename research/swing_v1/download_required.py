"""Fetch a fixed archive manifest; verify official SHA256 before atomic cache writes.

No catalogue expansion or alternate hosts. Access-policy errors abort the run.
This changes data coverage only, never signal or outcome rules.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import urllib.error
import urllib.parse
import urllib.request


def now():
    return datetime.now(timezone.utc).isoformat()


def download(item, cache, stop):
    info={k:item[k] for k in ('symbol','month','interval','url')}
    info['checked_at']=now()
    url=info['url']
    parsed=urllib.parse.urlsplit(url)
    if parsed.scheme!='https' or parsed.netloc!='data.binance.vision':
        raise ValueError('MANIFEST_HOST_NOT_OFFICIAL_ARCHIVE')
    name=f"{info['symbol']}-{info['interval']}-{info['month']}.zip"
    if Path(name).name!=name:raise ValueError('INVALID_CACHE_NAME')
    path=cache/name;tmp=cache/(name+f'.{os.getpid()}.part')
    if stop.is_set():return dict(info,status='CANCELLED')
    try:
        with urllib.request.urlopen(url+'.CHECKSUM',timeout=30) as r:
            checksum_text=r.read(4096).decode('utf-8')
        match=re.fullmatch(r'([a-fA-F0-9]{64})\s+\*?(.+?)\s*',checksum_text)
        if not match or match[2]!=name:raise ValueError('INVALID_OFFICIAL_CHECKSUM')
        expected=match[1].lower()
        prior=item.get('sha256')
        if prior and expected!=prior:raise ValueError('PREVIOUS_ARCHIVE_CHANGED')
        cached=path.exists()
        if not cached:
            if stop.is_set():return dict(info,status='CANCELLED')
            with urllib.request.urlopen(url,timeout=45) as r,tmp.open('wb') as f:
                while True:
                    block=r.read(1024*1024)
                    if not block:break
                    f.write(block)
        source=path if cached else tmp
        digest=hashlib.sha256(source.read_bytes()).hexdigest()
        if digest!=expected:raise ValueError('SHA256_MISMATCH')
        if not cached:tmp.replace(path)
        return dict(info,status='OK',source='EXISTING_CACHE' if cached else 'DOWNLOADED',
                    sha256=digest,bytes=path.stat().st_size,official_checksum=checksum_text.strip())
    except urllib.error.HTTPError as exc:
        if exc.code in (401,403,451):stop.set()
        return dict(info,status='NOT_PUBLISHED' if exc.code==404 else 'HTTP_ERROR',http_status=exc.code)
    except Exception as exc:
        message=str(exc)
        # urllib wraps proxy CONNECT denials in URLError, not HTTPError.
        denied = re.search(r'Tunnel connection failed:\s*(401|403|451)\b',message,re.I)
        if denied or 'blocked by policy' in message.lower() or isinstance(exc,ValueError):stop.set()
        return dict(info,status='ERROR',error=type(exc).__name__,detail=message)
    finally:
        if tmp.exists():tmp.unlink()


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--cache',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--workers',type=int,default=4)
    p.add_argument('--verified-log',type=Path,help='Reuse already verified records after rechecking their local hashes')
    args=p.parse_args()
    body=args.manifest.read_bytes();payload=json.loads(body)
    items=payload['archives']
    if len({(x['symbol'],x['month'],x['interval']) for x in items})!=len(items):
        raise ValueError('DUPLICATE_MANIFEST_ENTRY')
    args.cache.mkdir(parents=True,exist_ok=True);args.out.parent.mkdir(parents=True,exist_ok=True)
    if args.out.exists():raise ValueError('OUTPUT_ALREADY_EXISTS')
    stop=threading.Event();records=[];started=now();reused={}
    if args.verified_log:
        for line in args.verified_log.read_text().splitlines():
            old=json.loads(line)
            key=(old['symbol'],old['month'],old['interval'])
            if old['status']=='OK':
                path=args.cache/f"{old['symbol']}-{old['interval']}-{old['month']}.zip"
                if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest()!=old['sha256']:
                    raise ValueError('VERIFIED_RESUME_FILE_CHANGED')
                reused[key]=old
            elif old['status']=='NOT_PUBLISHED':reused[key]=old
    pending=[i for i in items if (i['symbol'],i['month'],i['interval']) not in reused]
    with args.out.with_suffix('.jsonl').open('x') as log,ThreadPoolExecutor(max_workers=args.workers) as pool:
        for item in items:
            key=(item['symbol'],item['month'],item['interval'])
            if key in reused:
                records.append(reused[key]);log.write(json.dumps(reused[key],ensure_ascii=False)+'\n')
        log.flush()
        futures=[pool.submit(download,item,args.cache,stop) for item in pending]
        for future in as_completed(futures):
            row=future.result();records.append(row)
            log.write(json.dumps(row,ensure_ascii=False)+'\n');log.flush()
            if len(records)%25==0 or row['status'] not in ('OK','CANCELLED'):
                print(json.dumps({'done':len(records),'total':len(items),
                    'counts':dict(Counter(r['status'] for r in records)),
                    'last':row['symbol'],'last_status':row['status']}),flush=True)
    report=dict(started_at=started,completed_at=now(),manifest_sha256=hashlib.sha256(body).hexdigest(),
                counts=dict(Counter(r['status'] for r in records)),aborted=stop.is_set(),
                total_verified_bytes=sum(r.get('bytes',0) for r in records),
                archives=sorted(records,key=lambda r:(r['symbol'],r['month'],r['interval'])))
    args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='archives'}),flush=True)
    if stop.is_set():raise SystemExit(2)


if __name__=='__main__':main()
