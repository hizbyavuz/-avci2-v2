"""Request settlement rates and mark-price minutes for ALL measured V1 plans."""
import hashlib
import json
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import quote

ROOT=Path(__file__).resolve().parents[2]


def main():
    source=ROOT/'research/swing_v1/results/2026-08-full-minute-audit.json'
    body=source.read_bytes();data=json.loads(body);items={};n=0
    for r in data['records']:
        if r['status']!='MEASURED':continue
        n+=1;s=r['symbol'];start=datetime.fromtimestamp(r['reference_ms']/1000,timezone.utc)
        end=datetime.fromtimestamp(r['outcome']['exit']['time_upper_ms']/1000,timezone.utc)
        month=start.replace(day=1,hour=0,minute=0,second=0,microsecond=0)
        while month<=end:
            stamp=month.strftime('%Y-%m')
            for kind in ('fundingRate','markPriceKlines'):
                interval=kind if kind=='fundingRate' else '1m'
                rel=f'data/futures/um/monthly/{kind}/{s}/'+('1m/' if kind=='markPriceKlines' else '')+f'{s}-{interval}-{stamp}.zip'
                items[s,kind,stamp]=dict(symbol=s,interval=interval,month=stamp,
                    url='https://data.binance.vision/'+quote(rel,safe='/'))
            month=month.replace(year=month.year+(month.month==12),month=month.month%12+1)
    out=Path(__file__).parent/'FUNDING_COST_REQUEST.json'
    if out.exists():raise ValueError('MANIFEST_EXISTS')
    out.write_text(json.dumps(dict(version='FUNDING_MARK_BOUNDS_V1',measured_plans=n,
        source_sha256=hashlib.sha256(body).hexdigest(),archives=list(items.values()),
        method='Settled rate times mark-minute low/high; no exact mark invention. Ambiguous exit minute uses a cost/receipt interval. Missing coverage stays unknown.'),indent=2)+'\n')
    print(json.dumps(dict(plans=n,archives=len(items))))


if __name__=='__main__':main()
