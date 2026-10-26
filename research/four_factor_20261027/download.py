"""Checksummed archive-only research downloader; never calls exchange REST.

Run separately from production. All network targets are static public archives.
403/418/429/451 stops the batch rather than retrying around an access restriction.
"""
import asyncio
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import shutil
import time
from urllib.parse import quote, urlencode
import xml.etree.ElementTree as ET

import aiohttp

BASE = 'https://data.binance.vision/'
CATALOG = 'https://s3-ap-northeast-1.amazonaws.com/data.binance.vision'
NS = {'s': 'http://s3.amazonaws.com/doc/2006-03-01/'}


def excluded_symbols(universe):
    from ta_paper.archive import EXCLUDED_BASES
    taxonomy=json.loads((Path(__file__).with_name('universe_classification.json')).read_text())
    excluded={s for b in taxonomy['verified_noncrypto'] for s in b['symbols']}
    excluded.update(taxonomy['uncertain_excluded']);excluded.update(taxonomy['index_excluded'])
    excluded.update(s for s in universe if s[:-4] in EXCLUDED_BASES)
    excluded.difference_update(taxonomy['include_override'])
    return sorted(set(universe)&excluded)


def atomic(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':'))+'\n')
    tmp.replace(path)


class Blocked(RuntimeError): pass
class Missing(FileNotFoundError): pass


class Download:
    def __init__(self, cache, concurrency=32, rps=64):
        self.cache=Path(cache);self.cache.mkdir(parents=True,exist_ok=True)
        self.sem=asyncio.Semaphore(concurrency);self.rps=rps
        self.pacing=asyncio.Lock();self.next_request=0.;self.blocked=None
        self.counts=Counter();self.records={};self.errors={};self.started=time.monotonic()
        self.checked=set();self.last_checkpoint=0.

    async def __aenter__(self):
        self.session=aiohttp.ClientSession(trust_env=True,
            connector=aiohttp.TCPConnector(limit=32),
            timeout=aiohttp.ClientTimeout(total=60),
            headers={'User-Agent':'TA-Paper-Checksummed-Archive-Study/1'})
        return self

    async def __aexit__(self,*args):await self.session.close()

    async def request(self,url):
        if not (url.startswith(BASE) or url.startswith(CATALOG+'?')):
            raise ValueError('ARCHIVE_HOST_ONLY')
        for attempt in range(3):
            if self.blocked:raise Blocked(self.blocked)
            async with self.sem:
                async with self.pacing:
                    delay=self.next_request-time.monotonic()
                    if delay>0:await asyncio.sleep(delay)
                    self.next_request=time.monotonic()+1/self.rps
                if self.blocked:raise Blocked(self.blocked)
                try:
                    async with self.session.get(url,allow_redirects=False) as r:
                        self.counts['http_requests']+=1
                        if r.status==404:raise Missing(url)
                        if r.status in (403,418,429,451):
                            self.blocked='ARCHIVE_HTTP_'+str(r.status)
                            raise Blocked(self.blocked)
                        if r.status!=200:raise RuntimeError('ARCHIVE_HTTP_'+str(r.status))
                        body=await r.read()
                        if len(body)>100_000_000:raise ValueError('ARCHIVE_TOO_LARGE')
                        self.counts['bytes']+=len(body)
                        return body
                except (Blocked,Missing,ValueError):raise
                except (aiohttp.ClientError,TimeoutError,RuntimeError):
                    if attempt==2:raise
            await asyncio.sleep(2**attempt)

    async def listing(self,prefix,delimiter=None):
        result=[];marker=''
        while True:
            q={'prefix':prefix,'max-keys':1000}
            if delimiter:q['delimiter']=delimiter
            if marker:q['marker']=marker
            root=ET.fromstring(await self.request(CATALOG+'?'+urlencode(q)))
            entries=[x.text for x in root.findall('s:CommonPrefixes/s:Prefix' if delimiter else 's:Contents/s:Key',NS)]
            result.extend(entries)
            if root.findtext('s:IsTruncated','false',NS)!='true':break
            marker=root.findtext('s:NextMarker','',NS) or entries[-1]
        return result

    async def file(self,key):
        if not key.startswith('data/futures/um/') or '..' in key.split('/') or not key.endswith('.zip'):
            raise ValueError('INVALID_ARCHIVE_KEY')
        path=self.cache/key;checksum=path.with_suffix('.sha256');missing=path.with_suffix('.missing')
        if missing.exists():
            self.counts['cached_missing']+=1;self.records[key]={'status':'MISSING_404'};return None
        if key in self.checked and path.exists():return path
        if path.exists() and checksum.exists():
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            if digest!=checksum.read_text().strip():raise ValueError('CACHED_CHECKSUM_MISMATCH')
            self.checked.add(key);self.records[key]={'sha256':digest,'bytes':path.stat().st_size}
            self.counts['cached']+=1;return path
        if shutil.disk_usage(self.cache).free<1024**3:raise RuntimeError('LESS_THAN_1_GIB_FREE')
        path.parent.mkdir(parents=True,exist_ok=True)
        url=BASE+quote(key,safe='/')
        try:
            expected=(await self.request(url+'.CHECKSUM')).decode().split()[0].lower()
            if not re.fullmatch('[0-9a-f]{64}',expected):raise ValueError('INVALID_CHECKSUM')
            body=await self.request(url)
        except Missing:
            missing.write_text('404 '+str(int(time.time()))+'\n');self.counts['missing']+=1
            self.records[key]={'status':'MISSING_404'}
            return None
        actual=hashlib.sha256(body).hexdigest()
        if actual!=expected:raise ValueError('CHECKSUM_MISMATCH')
        tmp=path.with_suffix('.part');tmp.write_bytes(body);tmp.replace(path)
        checksum.write_text(expected+'\n');self.checked.add(key)
        self.records[key]={'sha256':actual,'bytes':len(body)};self.counts['downloaded']+=1
        return path

    async def batch(self,keys,label='DOWNLOAD'):
        keys=sorted(set(keys));finished=0;last_log=time.monotonic()
        async def one(key):
            nonlocal finished,last_log
            try:await self.file(key)
            except Blocked:raise
            except Exception as e:self.errors[key]=type(e).__name__+':'+str(e)[:100]
            finally:
                finished+=1
                if time.monotonic()-last_log>=30 or finished==len(keys):
                    print(json.dumps({'phase':label,'finished':finished,'total':len(keys),
                        'counts':dict(self.counts),'errors':len(self.errors),
                        'seconds':round(time.monotonic()-self.started)},ensure_ascii=False),flush=True)
                    last_log=time.monotonic()
        # Bound task queues as well as in-flight HTTP connections.
        for begin in range(0,len(keys),240):
            await asyncio.gather(*(one(k) for k in keys[begin:begin+240]))
            if time.monotonic()-self.last_checkpoint>=60 or begin+240>=len(keys):
                atomic(self.cache/'download_inventory.json',dict(files=self.records,errors=self.errors,counts=dict(self.counts)))
                self.last_checkpoint=time.monotonic()


def key(symbol,tf,month,kind='klines'):
    return (f'data/futures/um/monthly/{kind}/{symbol}/{symbol}-{kind}-{month}.zip' if kind=='fundingRate'
            else f'data/futures/um/monthly/{kind}/{symbol}/{tf}/{symbol}-{tf}-{month}.zip')


async def coarse(cache, output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    async with Download(cache) as d:
        prefix='data/futures/um/monthly/klines/'
        folders=await d.listing(prefix,'/')
        universe=sorted({p[len(prefix):].strip('/') for p in folders if p.rstrip('/').endswith('USDT')})
        excluded=excluded_symbols(universe)
        universe=[s for s in universe if s not in excluded]
        manifest=dict(symbols=universe,excluded=excluded,month_files={},errors={},complete=False,
            classification_sha256=hashlib.sha256(Path(__file__).with_name('universe_classification.json').read_bytes()).hexdigest())
        atomic(output/'universe.json',manifest)
        month_set={'2025-09','2025-10','2025-11','2025-12',*[f'2026-{i:02}' for i in range(1,8)]}
        async def catalogue(symbol):
            try:
                files=await d.listing(prefix+symbol+'/30m/')
                selected=[f for f in files if f.endswith('.zip') and f[-11:-4] in month_set]
                manifest['month_files'][symbol]=selected
            except Blocked:raise
            except Exception as e:manifest['errors'][symbol]=type(e).__name__+':'+str(e)[:100]
        for i in range(0,len(universe),24):
            await asyncio.gather(*(catalogue(s) for s in universe[i:i+24]))
            atomic(output/'universe.json',manifest)
        keys=[]
        for symbol,files in manifest['month_files'].items():
            for f in files:
                month=f[-11:-4];keys.extend((f,key(symbol,'4h',month)))
        print(json.dumps({'phase':'CATALOGUED','symbols':len(universe),'files':len(keys),'errors':manifest['errors']}),flush=True)
        await d.batch(keys,'COARSE')
        manifest['download_errors']=d.errors;manifest['complete']=not manifest['errors'] and not d.errors
        atomic(output/'universe.json',manifest)


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--cache',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();asyncio.run(coarse(a.cache,a.output))
