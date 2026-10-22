"""Distinguish a missing checksum from a confirmed missing archive.

HEAD the same official archive URL for checksum-404 records; never infer a
delisting date. Preserve the original download report.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import urllib.error
import urllib.request


def verify(row):
    out=dict(row)
    try:
        with urllib.request.urlopen(urllib.request.Request(row['url'],method='HEAD'),timeout=25) as r:
            out.update(status='ARCHIVE_PRESENT_CHECKSUM_MISSING',archive_head_status=r.status)
    except urllib.error.HTTPError as exc:
        out['archive_head_status']=exc.code
        if exc.code==404:out['absence_confirmed_by_archive_head']=True
        else:out['status']='ARCHIVE_ABSENCE_UNKNOWN'
    except OSError as exc:
        out.update(status='ARCHIVE_ABSENCE_UNKNOWN',error=str(exc))
    return out


def main():
    p=argparse.ArgumentParser();p.add_argument('--input',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    if args.out.exists():raise ValueError('OUTPUT_ALREADY_EXISTS')
    report=json.loads(args.input.read_text())
    missing=[r for r in report['archives'] if r['status']=='NOT_PUBLISHED']
    with ThreadPoolExecutor(max_workers=6) as pool:checked=list(pool.map(verify,missing))
    lookup={(r['symbol'],r['month'],r['interval']):r for r in checked}
    report['archives']=[lookup.get((r['symbol'],r['month'],r['interval']),r) for r in report['archives']]
    report['counts']=dict(Counter(r['status'] for r in report['archives']))
    report['archive_absence_head_checks']=len(checked)
    report['original_download_report']=args.input.name
    args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='archives'},indent=2),flush=True)


if __name__=='__main__':main()
