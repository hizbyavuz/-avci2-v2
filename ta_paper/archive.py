"""Checksummed official public archive downloads; never calls live fapi."""
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

BASE='https://data.binance.vision/'
CATALOG='https://s3-ap-northeast-1.amazonaws.com/data.binance.vision'
EXCLUDED_BASES=frozenset('USDC FDUSD TUSD DAI USDP BUSD USDE USD1 XAU XAG XPT XPD XAUT PAXG CL BZ US500 US100 SPX SP500 NAS100 AAPL AMZN GOOGL GOOG META MSFT NVDA TSLA COIN MSTR'.split())


class ArchiveMissing(Exception): pass
class ArchiveBlocked(Exception): pass


class Archive:
    def __init__(self,cache):
        self.cache=Path(cache);self.cache.mkdir(parents=True,exist_ok=True)
        self.lock=threading.Lock();self.next_request=0
        self.blocked=False

    def request(self,url):
        with self.lock:
            if self.blocked:raise ArchiveBlocked('ARCHIVE_ACCESS_BLOCKED')
            delay=self.next_request-time.monotonic()
            if delay>0:time.sleep(delay)
            self.next_request=time.monotonic()+.15
        for attempt in range(3):
            try:
                with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'TA-Paper-Archive-Research/1.1'}),timeout=30) as r:
                    data=r.read(100_000_001)
                    if len(data)>100_000_000:raise ValueError('ARCHIVE_FILE_TOO_LARGE')
                    return data
            except urllib.error.HTTPError as e:
                if e.code==404:raise ArchiveMissing(url.removeprefix(BASE)) from None
                if e.code in (403,418,429,451):
                    self.blocked=True
                    raise ArchiveBlocked('ARCHIVE_HTTP_'+str(e.code)) from None
                if e.code<500 or attempt==2:raise
            except (TimeoutError,urllib.error.URLError):
                if attempt==2:raise
            time.sleep(min(2**attempt,4))

    def symbols(self):
        path=self.cache/'symbol_catalog.json'
        if path.exists():return json.loads(path.read_text())
        prefix='data/futures/um/monthly/klines/'
        marker='';symbols=[]
        ns={'s':'http://s3.amazonaws.com/doc/2006-03-01/'}
        while True:
            query={'delimiter':'/','prefix':prefix,'max-keys':'1000'}
            if marker:query['marker']=marker
            root=ET.fromstring(self.request(CATALOG+'?'+urllib.parse.urlencode(query)))
            prefixes=[x.text for x in root.findall('s:CommonPrefixes/s:Prefix',ns)]
            symbols.extend(x[len(prefix):].strip('/') for x in prefixes)
            if root.findtext('s:IsTruncated','false',ns)!='true':break
            marker=root.findtext('s:NextMarker','',ns) or prefixes[-1]
        result=sorted(set(s for s in symbols if re.fullmatch(r'[A-Z0-9]+USDT',s)))
        path.write_text(json.dumps(result,indent=2)+'\n')
        return result

    def key(self,symbol,interval,month,kind='klines'):
        if not re.fullmatch(r'[A-Z0-9]+USDT',symbol) or not re.fullmatch(r'\d{4}-\d{2}',month):
            raise ValueError('INVALID_ARCHIVE_KEY')
        if kind=='fundingRate':return f'data/futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{month}.zip'
        if kind not in ('klines','markPriceKlines') or interval not in ('1m','30m','4h'):
            raise ValueError('INVALID_ARCHIVE_DATASET')
        return f'data/futures/um/monthly/{kind}/{symbol}/{interval}/{symbol}-{interval}-{month}.zip'

    def file(self,symbol,interval,month,kind='klines'):
        key=self.key(symbol,interval,month,kind)
        path=self.cache/key
        missing=path.with_suffix('.missing')
        checksum=path.with_suffix('.sha256')
        if missing.exists():raise ArchiveMissing(key)
        if path.exists() and checksum.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest()==checksum.read_text().strip():return path
            raise ValueError('CACHED_ARCHIVE_CHECKSUM_MISMATCH')
        path.parent.mkdir(parents=True,exist_ok=True)
        try:
            expected=self.request(BASE+key+'.CHECKSUM').decode().split()[0]
            if not re.fullmatch('[0-9a-fA-F]{64}',expected):raise ValueError('INVALID_ARCHIVE_CHECKSUM')
            data=self.request(BASE+key)
        except ArchiveMissing:
            missing.write_text('404 at '+time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())+'\n')
            raise
        if hashlib.sha256(data).hexdigest().lower()!=expected.lower():raise ValueError('ARCHIVE_CHECKSUM_MISMATCH')
        tmp=path.with_suffix('.tmp');tmp.write_bytes(data);tmp.replace(path)
        checksum.write_text(expected.lower()+'\n')
        return path

    def rows(self,symbol,interval,months,kind='klines',optional=False):
        rows=[]
        for month in months:
            try:path=self.file(symbol,interval,month,kind)
            except ArchiveMissing:
                if optional:continue
                raise
            with zipfile.ZipFile(path) as z:
                names=z.namelist()
                if len(names)!=1 or z.getinfo(names[0]).file_size>300_000_000:raise ValueError('UNEXPECTED_ARCHIVE_CONTENT')
                with z.open(names[0]) as f:
                    for row in csv.reader(io.TextIOWrapper(f,encoding='utf-8-sig')):
                        if not row or not row[0].strip().isdigit():continue
                        if kind=='fundingRate':
                            rows.append([int(row[0]),float(row[-1]),float(row[1])])
                        else:
                            if len(row)<8:raise ValueError('SHORT_ARCHIVE_ROW')
                            stamp=int(row[0]);end=int(row[6])
                            if stamp>100_000_000_000_000:raise ValueError('UNEXPECTED_FUTURES_TIME_UNIT')
                            rows.append([stamp,*map(float,row[1:6]),end,float(row[7])])
        rows.sort(key=lambda r:r[0])
        if len({r[0] for r in rows})!=len(rows):raise ValueError('ARCHIVE_DUPLICATE_TIMESTAMP')
        return rows

    def coarse(self,month='2026-08',warmup='2026-07',workers=4):
        symbols=self.symbols()
        result={'month':month,'warmup':warmup,'catalog_count':len(symbols),'ready':[],
                'excluded':[],'missing':[],'errors':{},'classification':'NAME_BASED_APPROXIMATION_NOT_POINT_IN_TIME_METADATA'}
        selected=[]
        for s in symbols:
            if s[:-4] in EXCLUDED_BASES:result['excluded'].append(s)
            else:selected.append(s)
        def get(s):
            self.file(s,'30m',month)
            for tf,m in [('30m',warmup),('4h',month),('4h',warmup)]:
                try:self.file(s,tf,m)
                except ArchiveMissing:pass
            return s
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures={pool.submit(get,s):s for s in selected}
            for i,f in enumerate(as_completed(futures),1):
                s=futures[f]
                try:result['ready'].append(f.result())
                except ArchiveMissing:result['missing'].append(s)
                except Exception as e:result['errors'][s]=type(e).__name__
                if i%20==0:
                    print(json.dumps({'completed':i,'total':len(selected),'ready':len(result['ready']),
                                      'missing':len(result['missing']),'errors':len(result['errors'])}),flush=True)
                    tmp=self.cache/'coarse_manifest.tmp';tmp.write_text(json.dumps(result,indent=2)+'\n');tmp.replace(self.cache/'coarse_manifest.json')
        for key in ('ready','excluded','missing'):result[key].sort()
        tmp=self.cache/'coarse_manifest.tmp';tmp.write_text(json.dumps(result,indent=2)+'\n');tmp.replace(self.cache/'coarse_manifest.json')
        return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cache',required=True);p.add_argument('--month',default='2026-08')
    p.add_argument('--warmup',default='2026-07');p.add_argument('--workers',type=int,default=4)
    args=p.parse_args()
    if not 1<=args.workers<=4:raise ValueError('workers must be 1..4')
    r=Archive(args.cache).coarse(args.month,args.warmup,args.workers)
    print(json.dumps({k:len(v) if isinstance(v,(list,dict)) else v for k,v in r.items()},indent=2))


if __name__=='__main__':main()
