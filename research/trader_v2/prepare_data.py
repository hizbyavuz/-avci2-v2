"""Fixed retrospective context request; never selects candidates by outcome."""
import hashlib
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import quote

ROOT=Path(__file__).resolve().parents[2]


def main():
    out=Path(__file__).parent/'DATA_REQUEST.json'
    if out.exists():raise ValueError('MANIFEST_ALREADY_EXISTS')
    items={};sources=[];total=0
    for name in ('2026-08-screen.json','2026-09-screen-complete.json'):
        p=ROOT/'research/swing_v1/results'/name;body=p.read_bytes()
        data=json.loads(body);sources.append(dict(path=str(p.relative_to(ROOT)),sha256=hashlib.sha256(body).hexdigest()))
        for r in data['records']:
            total+=1;p=r['plan'];symbol=p['symbol']
            dt=datetime.fromtimestamp(p['confirmation_ms']/1000,timezone.utc)
            for day in (dt.date()-timedelta(days=1),dt.date()):
                stamp=str(day);kind='metrics'
                path=f'data/futures/um/daily/{kind}/{symbol}/{symbol}-{kind}-{stamp}.zip'
                items[symbol,kind,stamp]=dict(symbol=symbol,interval=kind,month=stamp,
                    url='https://data.binance.vision/'+quote(path,safe='/'))
            stamp=dt.strftime('%Y-%m');kind='fundingRate'
            path=f'data/futures/um/monthly/{kind}/{symbol}/{symbol}-{kind}-{stamp}.zip'
            items[symbol,kind,stamp]=dict(symbol=symbol,interval=kind,month=stamp,
                url='https://data.binance.vision/'+quote(path,safe='/'))
    payload=dict(version='TRADER_CONTEXT_ARCHIVES_V1',candidates=total,sources=sources,
        protocol_sha256=hashlib.sha256((Path(__file__).parent/'PROTOCOL.md').read_bytes()).hexdigest(),
        archives=list(items.values()))
    out.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps(dict(candidates=total,archives=len(items),manifest_sha256=hashlib.sha256(out.read_bytes()).hexdigest())))


if __name__=='__main__':main()
