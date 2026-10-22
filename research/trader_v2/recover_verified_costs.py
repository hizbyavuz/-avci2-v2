"""Reuse matching already-verified local funding files. No network requests."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil


def main():
    p=argparse.ArgumentParser();p.add_argument('--context-cache',type=Path,required=True)
    p.add_argument('--cost-cache',type=Path,required=True);args=p.parse_args()
    here=Path(__file__).parent;raw=(here/'FUNDING_COST_DOWNLOAD.json').read_bytes()
    prior_raw=(here/'DOWNLOAD.json').read_bytes();report=json.loads(raw);prior=json.loads(prior_raw)
    by_url={r['url']:r for r in prior['archives'] if r['status']=='OK'};recovered=[]
    for i,row in enumerate(report['archives']):
        old=by_url.get(row['url'])
        if row['status']=='OK' or not old:continue
        if any(row[k]!=old[k] for k in ('symbol','month','interval')):raise ValueError('SOURCE_IDENTITY_CHANGED')
        name=f"{row['symbol']}-{row['interval']}-{row['month']}.zip"
        source=args.context_cache/name;target=args.cost_cache/name
        if hashlib.sha256(source.read_bytes()).hexdigest()!=old['sha256']:raise ValueError('PRIOR_VERIFIED_FILE_CHANGED')
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest()!=old['sha256']:raise ValueError('COST_CACHE_CONFLICT')
        else:shutil.copyfile(source,target)
        report['archives'][i]=dict(old,source='OFFLINE_PRIOR_VERIFIED',
            superseded_download_status=row['status'],prior_report_sha256=hashlib.sha256(prior_raw).hexdigest())
        recovered.append(row['url'])
    report['counts']=dict(Counter(r['status'] for r in report['archives']))
    report['total_verified_bytes']=sum(r.get('bytes',0) for r in report['archives'] if r['status']=='OK')
    report['offline_recovery']=dict(network_requests=0,count=len(recovered),
        original_report_sha256=hashlib.sha256(raw).hexdigest(),
        prior_report_sha256=hashlib.sha256(prior_raw).hexdigest(),urls=recovered)
    out=here/'FUNDING_COST_OFFLINE_RECOVERY.json'
    if out.exists():raise ValueError('OUTPUT_EXISTS')
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(recovered=len(recovered),counts=report['counts'])))


if __name__=='__main__':main()
