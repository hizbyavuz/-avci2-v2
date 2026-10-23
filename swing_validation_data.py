"""Isolated official archive reader. Existing caches are read-only inputs."""
import argparse, csv, gzip, hashlib, io, json, threading, time, zipfile
from pathlib import Path
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import xml.etree.ElementTree as ET
import requests
import numpy as np

HOUR=3600000; DAY=24*HOUR; MINUTE=60000
START=1577836800000; END=1792713600000
BASE='https://data.binance.vision/'
CATALOG='https://s3-ap-northeast-1.amazonaws.com/data.binance.vision'
_local=threading.local()
# Identity registry for retired symbols absent from current exchangeInfo.
# Does not use current TRADING status, returns, or delisting dates for selection.
RETIRED_COINS=dict(zip('1000BTTC AERGO AKRO ANC ANT AUDIO BDXN BTCST BTS BTT BZRX COCOS DODO EOS FRONT GAL HNT KEEP LEND LUNA MATIC MBL NU RNDR SRM SXP TOMO YFII'.split(),
    ['BitTorrent','Aergo','Akropolis','Anchor','Aragon','Audius','Bondex','Bitcoin Standard Hashrate Token','BitShares','BitTorrent','bZx','Cocos-BCX','DODO','EOS','Frontier','Galxe','Helium','Keep','ETHLend','Terra','Polygon','MovieBloc','NuCypher','Render','Serum','Swipe','TomoChain','DFI.Money']))
RETIRED_INDICES={'BLUEBIRDUSDT','DOTECOUSDT','FOOTBALLUSDT'}
def iso(t):return datetime.fromtimestamp(t/1000,timezone.utc).isoformat()
def ms(t):return int(datetime.fromisoformat(t).replace(tzinfo=timezone.utc).timestamp()*1000)
def months(a,b):
    d=datetime.fromtimestamp(a/1000,timezone.utc).replace(day=1,hour=0,minute=0,second=0,microsecond=0)
    while d.timestamp()*1000<b:
        yield d.strftime('%Y-%m');d=(d.replace(day=28)+timedelta(days=4)).replace(day=1)
def save(path,x):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix(path.suffix+'.partial')
    if path.suffix=='.gz':
        with gzip.open(tmp,'wt') as f:json.dump(x,f,separators=(',',':'))
    else:tmp.write_text(json.dumps(x,ensure_ascii=False,indent=2))
    tmp.replace(path)
def read(path):
    path=Path(path)
    return json.loads(gzip.decompress(path.read_bytes()) if path.suffix=='.gz' else path.read_bytes())
def get(url,params=None):
    if not hasattr(_local,'session'):_local.session=requests.Session()
    for n in range(3):
        try:
            r=_local.session.get(url,params=params,timeout=(10,30))
            if r.status_code==404:return None
            r.raise_for_status();return r.content
        except requests.RequestException:
            if n==2:raise
            time.sleep(.3*(n+1))

class Archive:
    def __init__(self,root,sources=()):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.sources=[Path(p) for p in sources];self.lock=threading.Lock()
    def note(self,row):
        with self.lock,(self.root/'downloads.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
    def listing(self,prefix):
        name=hashlib.sha256((prefix+'/').encode()).hexdigest()
        for base in [self.root]+self.sources:
            path=base/'catalog'/name
            if path.exists():return read(path)
        ns={'s':'http://s3.amazonaws.com/doc/2006-03-01/'};keys=[];prefixes=[];marker=''
        while True:
            raw=get(CATALOG,dict(prefix=prefix,delimiter='/',marker=marker))
            if raw is None:raise ValueError('CATALOG_MISSING')
            x=ET.fromstring(raw);kk=[v.text for v in x.findall('s:Contents/s:Key',ns)];pp=[v.text for v in x.findall('s:CommonPrefixes/s:Prefix',ns)]
            keys+=kk;prefixes+=pp
            if x.findtext('s:IsTruncated',namespaces=ns)!='true':break
            new=x.findtext('s:NextMarker',namespaces=ns) or (kk+pp)[-1]
            if new==marker:raise ValueError('CATALOG_STALLED')
            marker=new
        out=dict(keys=keys,prefixes=prefixes);save(self.root/'catalog'/name,out);return out
    def rows(self,key):
        for base in [self.root]+self.sources:
            p=base/key
            if p.exists() and p.with_suffix('.sha256').exists():
                raw=p.read_bytes()
                if hashlib.sha256(raw).hexdigest()!=p.with_suffix('.sha256').read_text().strip():raise ValueError('CHECKSUM_CHANGED '+key)
                return self.parse(raw)
        dest=self.root/key
        if dest.with_suffix('.404').exists():return []
        raw=get(BASE+requests.utils.quote(key,safe='/'))
        if raw is None:
            dest.parent.mkdir(parents=True,exist_ok=True);dest.with_suffix('.404').write_text('404');self.note(dict(key=key,status='NOT_PUBLISHED'));return []
        check=get(BASE+requests.utils.quote(key+'.CHECKSUM',safe='/'));sha=hashlib.sha256(raw).hexdigest()
        if check is None or check.decode().split()[0]!=sha:raise ValueError('OFFICIAL_CHECKSUM_FAILED '+key)
        dest.parent.mkdir(parents=True,exist_ok=True);tmp=dest.with_suffix('.partial');tmp.write_bytes(raw);tmp.replace(dest);dest.with_suffix('.sha256').write_text(sha)
        self.note(dict(key=key,status='OK',sha256=sha,bytes=len(raw)))
        return self.parse(raw)
    @staticmethod
    def parse(raw):
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            names=z.namelist()
            if len(names)!=1:raise ValueError('BAD_ZIP')
            return [r for r in csv.reader(io.StringIO(z.read(names[0]).decode())) if r and r[0].isdigit()]
    def klines(self,s,period,interval='1h',daily=False,kind='klines'):
        return self.rows(f'data/futures/um/{"daily" if daily else "monthly"}/{kind}/{s}/{interval}/{s}-{interval}-{period}.zip')
    def funding(self,s,m):
        return self.rows(f'data/futures/um/monthly/fundingRate/{s}/{s}-fundingRate-{m}.zip')

def bars(rows):
    # [open_ms,O,H,L,C,quote_volume,taker_buy_quote,base_volume]
    out=np.array([[int(r[0]),*map(float,r[1:5]),float(r[7]),float(r[10]),float(r[5])] for r in rows],dtype=np.float64)
    if not len(out):return np.empty((0,8))
    if np.any(~np.isfinite(out)) or np.any(out[:,1:5]<=0) or np.any(out[:,5:]<0):raise ValueError('INVALID_RAW_CANDLE')
    return out

def prepare(root,sources,workers=12):
    root=Path(root);a=Archive(root/'archive',sources);dest=root/'prepared';dest.mkdir(parents=True,exist_ok=True)
    meta_path=root/'exchange_identity.json'
    if not meta_path.exists():
        raw=get('https://www.binance.com/fapi/v1/exchangeInfo');x=json.loads(raw)
        save(meta_path,dict(fetched_at=datetime.now(timezone.utc).isoformat(),source='https://www.binance.com/fapi/v1/exchangeInfo',symbols=[{k:s.get(k) for k in ['symbol','contractType','underlyingType','underlyingSubType','quoteAsset','marginAsset','status','onboardDate','deliveryDate']} for s in x['symbols']]))
    meta={s['symbol']:s for s in read(meta_path)['symbols']}
    catalog=sorted(p.split('/')[-2] for p in a.listing('data/futures/um/monthly/klines/')['prefixes'] if p.split('/')[-2].endswith('USDT') and '_' not in p.split('/')[-2])
    classification=[];selected=[]
    for s in catalog:
        m=meta.get(s)
        status='COIN' if (m and m['contractType']=='PERPETUAL' and m['underlyingType']=='COIN') or s[:-4] in RETIRED_COINS else 'NON_CRYPTO' if s in RETIRED_INDICES or m and (m['underlyingType'] in ['EQUITY','COMMODITY','INDEX'] or m['contractType']=='TRADIFI_PERPETUAL') else 'UNVERIFIED_IDENTITY'
        classification.append(dict(symbol=s,classification=status,metadata=m,retired_asset_identity=RETIRED_COINS.get(s[:-4]),identity_source='exchangeInfo' if m else 'explicit_retired_asset_registry'))
        if status=='COIN':selected.append(s)
    save(root/'classification.json',classification)
    def one(s):
        p=dest/(s+'.npz')
        if p.exists():
            z=np.load(p);x=z['bars'];return dict(symbol=s,rows=len(x),first=int(x[0,0]),last=int(x[-1,0]),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
        keys=a.listing(f'data/futures/um/monthly/klines/{s}/1h/')['keys'];rows=[]
        for k in keys:
            if k.endswith('.zip') and ms(k[-11:-4]+'-01')<END:rows+=a.rows(k)
        present={int(r[0]):r for r in rows if int(r[0])<END}
        if present:
            # Only current month of contracts still showing activity; historical delists are retained.
            last=max(present)
            if last>=ms('2026-09-01'):
                for t in range(ms('2026-10-01'),END,DAY):
                    for r in a.klines(s,iso(t)[:10],daily=True):
                        if int(r[0])<END:present[int(r[0])]=r
        if not present:return dict(symbol=s,rows=0,error='NO_HISTORICAL_DATA')
        arr=bars([present[t] for t in sorted(present)])
        tmp=p.with_suffix('.partial')
        with tmp.open('wb') as f:np.savez_compressed(f,bars=arr)
        tmp.replace(p)
        return dict(symbol=s,rows=len(arr),first=int(arr[0,0]),last=int(arr[-1,0]),sha256=hashlib.sha256(p.read_bytes()).hexdigest(),gaps=int(np.sum(np.diff(arr[:,0])!=HOUR)))
    records=[];errors=[]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        jobs={pool.submit(one,s):s for s in selected}
        for i,f in enumerate(as_completed(jobs),1):
            try:records.append(f.result())
            except Exception as e:errors.append(dict(symbol=jobs[f],error=type(e).__name__+':'+str(e)[:180]))
            if i%20==0 or i==len(jobs):print(json.dumps(dict(stage='prepare',done=i,total=len(jobs),errors=len(errors))),flush=True)
    save(root/'manifest.json',dict(start=START,end=END,symbols=sorted(records,key=lambda x:x['symbol']),errors=errors,catalog_count=len(catalog),classified_coins=len(selected),unknown=[x['symbol'] for x in classification if x['classification']=='UNVERIFIED_IDENTITY']))
    print(json.dumps(dict(stage='prepared',symbols=len(records),errors=errors)),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data',required=True);p.add_argument('--source',action='append',default=[]);p.add_argument('--workers',type=int,default=12);args=p.parse_args();prepare(args.data,args.source,args.workers)
