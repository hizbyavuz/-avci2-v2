"""Bounded synthetic load check, not a live-data or profitability test."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import time

from .core import CFG, MINUTE
from .io import Store
from .runner import scan


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--symbols',type=int,default=300)
    args=parser.parse_args()
    if not 1<=args.symbols<=1000:
        raise ValueError('symbols must be 1..1000')
    now=200*CFG.context_ms+2*MINUTE
    symbols=[f'SYN{i}USDT' for i in range(args.symbols)]
    class Synthetic:
        def now(self): return now
        def sync_time(self): pass
        def get(self,path,**kw):
            if path.endswith('exchangeInfo'):
                return {'symbols':[dict(symbol=s,status='TRADING',contractType='PERPETUAL',
                    quoteAsset='USDT',marginAsset='USDT',baseAsset=s[:-4],underlyingType='COIN',
                    underlyingSubType=[],onboardDate=1) for s in symbols]}
            if path.endswith('24hr'):
                return [dict(symbol=s,quoteVolume='3000000',closeTime=now) for s in symbols]
            raise AssertionError('Flat synthetic series must not generate a candidate')
        def candles(self,symbol,tf,*args,**kw):
            period=CFG.entry_ms if tf=='30m' else CFG.context_ms
            end=now//period*period
            return [[end-(120-i)*period,100,101,99,100,100,end-(119-i)*period-1] for i in range(120)]
    with tempfile.TemporaryDirectory() as folder:
        path=Path(folder)/'benchmark.sqlite3'
        store=Store(path)
        health=scan(store,Synthetic())
        health.update(data='SYNTHETIC_NO_NETWORK', profitability_measured=False,
                      sqlite_bytes=path.stat().st_size)
        print(json.dumps(health,indent=2))


if __name__=='__main__':
    main()
