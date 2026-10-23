"""Run the original monthly hourly screen from a verified complete catalogue."""
import argparse
from collections import Counter
import json
from pathlib import Path

import replay
from long_short_swing import CONFIG_HASH, VERSION


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--cache',type=Path,required=True)
    p.add_argument('--download',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    if args.out.exists():raise ValueError('OUTPUT_ALREADY_EXISTS')
    download=json.loads(args.download.read_text());data={}
    if download['aborted'] or any(a['status'] not in ('OK','NOT_PUBLISHED') for a in download['archives']):
        raise ValueError('INCOMPLETE_CATALOGUE_DOWNLOAD')
    months={a['month'] for a in download['archives']}
    if len(months)!=1:raise ValueError('SINGLE_MONTH_REQUIRED')
    for a in download['archives']:
        if a['status']=='NOT_PUBLISHED':continue
        info,rows=replay.archive(args.cache,a['symbol'],a['month'],'1h',offline=True)
        if info['status']!='OK' or info['sha256']!=a['sha256']:
            raise ValueError('VERIFIED_ARCHIVE_CHANGED:'+a['symbol'])
        data[a['symbol']]=rows
    print(json.dumps(dict(screen_started=True,available_symbols=len(data))),flush=True)
    report=replay.screen(data)
    report.update(month=next(iter(months)),version=VERSION,config_hash=CONFIG_HASH,
        summary=replay.summarize(report['records']),
        universe_version='UNCLASSIFIED_USDT_ARCHIVE_V1',crypto_only=False,
        classification='FROZEN_RULES_SECOND_MONTH_RAW_DESIGN_CHECK_NOT_TRADE_PROFIT',
        archive_statuses=dict(Counter(a['status'] for a in download['archives'])),
        catalogue_symbols=len(download['archives']),network_used=False)
    args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k not in ('records','rejections')},ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':main()
