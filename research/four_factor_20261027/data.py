"""Causal, bounded archive reads and frozen-core discovery for the study."""
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
import zipfile

import numpy as np

from ta_paper.core import CFG, MINUTE, assess, ema
from ta_paper.features import kline_aux, relative_strength
from .download import key, atomic

DAY=1440*MINUTE
ROOT=Path(__file__).resolve().parent
PROTOCOL=json.loads((ROOT/'protocol.json').read_text())
SEED=PROTOCOL['seed']
START=int(datetime(2025,10,1,tzinfo=timezone.utc).timestamp()*1000)
END=int(datetime(2026,8,1,tzinfo=timezone.utc).timestamp()*1000)
MONTHS=['2025-09','2025-10','2025-11','2025-12',*[f'2026-{i:02}' for i in range(1,8)]]


def digest(text):return hashlib.sha256((SEED+':'+text).encode()).hexdigest()


def read_rows(cache,symbol,tf,months,kind='klines'):
    rows=[];issues=[]
    columns=3 if kind=='fundingRate' else 11 if kind=='klines' and tf!='1m' else 7
    period={'1m':MINUTE,'30m':30*MINUTE,'4h':240*MINUTE}.get(tf)
    for month in months:
        path=Path(cache)/key(symbol,tf,month,kind)
        if not path.exists():
            issues.append(dict(month=month,kind=kind,tf=tf,reason='MISSING_FILE'));continue
        try:
            checksum=path.with_suffix('.sha256').read_text().strip()
            blob=path.read_bytes()
            if hashlib.sha256(blob).hexdigest()!=checksum:raise ValueError('CHECKSUM_MISMATCH')
            with zipfile.ZipFile(io.BytesIO(blob)) as zipped:
                files=zipped.infolist()
                if len(files)!=1 or files[0].file_size>300_000_000:raise ValueError('ZIP_CONTENT')
                with zipped.open(files[0]) as source:
                    first=source.readline().decode('utf-8-sig')
                    header=not first.split(',')[0].strip().isdigit()
                with zipped.open(files[0]) as source:
                    data=np.loadtxt(source,delimiter=',',skiprows=int(header),usecols=range(columns),ndmin=2)
            if not np.isfinite(data).all():raise ValueError('NON_FINITE_ARCHIVE')
            if len(data)==0:raise ValueError('EMPTY_ARCHIVE')
            if kind=='fundingRate':
                if (data[:,0]%1).any() or (data[:,1]<=0).any():raise ValueError('FUNDING_SCHEMA')
                data=data[:,[0,2,1]]  # timestamp, settled rate, interval hours
            else:
                if (data[:,0]%period).any() or not (data[:,6]==data[:,0]+period-1).all():raise ValueError('CANDLE_TIME')
                if ((data[:,1:5]<=0).any() or (data[:,5]<0).any() or
                    (data[:,2]<np.maximum(data[:,1],data[:,4])).any() or
                    (data[:,3]>np.minimum(data[:,1],data[:,4])).any() or (data[:,2]<data[:,3]).any()):
                    raise ValueError('CANDLE_OHLC')
            expected=month
            if any(datetime.fromtimestamp(int(t)/1000,timezone.utc).strftime('%Y-%m')!=expected for t in (data[0,0],data[-1,0])):
                raise ValueError('WRONG_ARCHIVE_MONTH')
            rows.append(data)
        except Exception as e:issues.append(dict(month=month,kind=kind,tf=tf,reason=type(e).__name__+':'+str(e)[:100]))
    if not rows:return np.empty((0,columns)),issues
    result=np.concatenate(rows);result=result[np.argsort(result[:,0])]
    duplicates=np.where(np.diff(result[:,0])==0)[0]
    for i in duplicates:
        if not np.array_equal(result[i],result[i+1]):raise ValueError('CONFLICTING_ARCHIVE_ROWS')
    if len(duplicates):result=np.delete(result,duplicates,axis=0)
    return result,issues


def as_rows(a):
    result=a.tolist()
    for r in result:r[0]=int(r[0]);r[6]=int(r[6])
    return result


def weights(n,period=20):
    alpha=2/(period+1);w=np.full(n,(1-alpha)**(n-period)/period)
    w[period:]=alpha*(1-alpha)**np.arange(n-period-1,-1,-1)
    return w


def screen_reclaims(bars):
    """Conservative vector pre-screen; core.assess remains the final authority.

    EMA[-2] uses the SAME 119-bar seed as EMA[-1], not a shifted window.
    A tolerance includes near-boundary cases; it never independently admits one.
    """
    n=CFG.history_bars-1
    mask=np.zeros(len(bars),dtype=bool)
    if len(bars)<n:return mask
    windows=np.lib.stride_tricks.sliding_window_view(bars[:,4],n)
    previous=windows[:,:-1]@weights(n-1)
    current=previous*(1-2/21)+windows[:,-1]*(2/21)
    last=bars[n-1:];prior=bars[n-2:-1];tol=np.maximum(last[:,4],prior[:,4])*1e-10
    mask[n-1:]=(((prior[:,4]<=previous+tol)&(last[:,4]>=current-tol)&(last[:,4]>=prior[:,2]-tol))|
                ((prior[:,4]>=previous-tol)&(last[:,4]<=current+tol)&(last[:,4]<=prior[:,3]+tol)))
    return mask


class Discovery:
    def __init__(self,symbol,bars,context,btc):
        self.symbol=symbol;self.b=bars;self.c=context;self.btc=btc
        self.ct=context[:,0].tolist();self.bt=btc[:,0].tolist();self.saved={};self.counts=Counter()
        self.valid={};self.context_cache={};self.btc_cache={}
        self.days=defaultdict(list)
        n=CFG.history_bars-1
        for i in range(n-1,len(bars)):
            stamp=int(bars[i,0]);now=stamp+CFG.entry_ms+PROTOCOL['decision_latency_ms']
            if not START<=stamp<END:continue
            if now-bars[0,0]<CFG.min_listing_days*DAY:continue
            if (np.diff(bars[i-n+1:i+1,0])!=CFG.entry_ms).any():continue
            if sum(bars[i-47:i+1,7])<CFG.min_quote_volume:continue
            self.valid[i]=now;self.days[now//DAY].append(i)
        self.counts['eligible_30m_grid']=len(self.valid)

    def get(self,i):
        if i in self.saved:return self.saved[i]
        now=self.valid[i];n=CFG.history_bars-1
        last=bisect_right(self.ct,now-CFG.publication_delay_ms-CFG.context_ms)
        c=self.context_cache.setdefault(last,as_rows(self.c[max(0,last-n):last]))
        bars=as_rows(self.b[i-n+1:i+1])
        try:p=assess(self.symbol,bars,c,now)
        except (ValueError,IndexError) as e:
            self.counts['invalid:'+str(e)]+=1;self.saved[i]=None;return None
        self.counts['core:'+p['reason']]+=1
        self.saved[i]=(p,bars,c)
        return self.saved[i]

    def feature(self,p,bars,context):
        now=p['observed_ms'];n=CFG.history_bars-1
        end=bisect_right(self.bt,now-CFG.publication_delay_ms-CFG.context_ms)
        btc=self.btc_cache.setdefault(end,as_rows(self.btc[max(0,end-n):end]))
        fields=dict(taker_buy_share=None,price_change_4h=bars[-1][4]/bars[-9][4]-1,
                    btc_relative_3d=relative_strength(context,btc),btc_regime='UNKNOWN',
                    oi_change_4h=None,funding_percentile_30d=None)
        try:fields['taker_buy_share']=kline_aux(bars[-1])['taker_buy_share']
        except ValueError:fields['taker_issue']='INVALID_TAKER_FIELDS'
        if len(btc)>=80 and btc[-1][0]==context[-1][0] and all(y[0]-x[0]==CFG.context_ms for x,y in zip(btc,btc[1:])):
            f=ema([r[4] for r in btc],20);s=ema([r[4] for r in btc],50)
            fields['btc_regime']='UP' if btc[-1][4]>f[-1]>s[-1] and f[-1]>f[-4] else 'DOWN' if btc[-1][4]<f[-1]<s[-1] and f[-1]<f[-4] else 'SIDEWAYS'
        return fields

    def decorated(self,p,bars,context,arm):
        p=dict(p,arm=arm,quote_volume=sum(r[7] for r in bars[-48:]),
               id=digest(f'{arm}:{self.symbol}:{p["candle_ms"]}:{p["direction"]}'))
        p['features']=self.feature(p,bars,context)
        return p

    def timing(self,source,original):
        group=self.days[source['observed_ms']//DAY]
        order=sorted((i for i in group if i!=original),key=lambda i:digest(source['id']+':timing:'+str(int(self.b[i,0]))))
        sign=1 if source['direction']=='LONG' else -1
        for i in order:
            result=self.get(i)
            if result is None:continue
            p,b,c=result
            if p.get('trend')!=sign or p['atr']<=0:continue
            a=p['atr'];last=b[-1];tail=b[-CFG.pullback_bars:]
            stop=min(r[3] for r in tail)-.1*a if sign==1 else max(r[2] for r in tail)+.1*a
            distance=sign*(last[4]-stop)
            if last[2]-last[3]>CFG.max_trigger_range_atr*a or not CFG.min_stop_fraction<=distance/last[4]<=CFG.max_stop_fraction:continue
            now=p['observed_ms']
            p=dict(p,status='SETUP',reason='HASHED_RANDOM_TIMING',stop=stop,target=last[4]+sign*2*distance,
                   entry_limit=last[4]+sign*CFG.max_chase_atr*a,
                   entry_ms=((now+CFG.entry_delay_ms)//MINUTE+1)*MINUTE,expires_ms=now+CFG.max_pending_ms)
            return self.decorated(p,b,c,'random_timing')
        return None

    def run(self):
        screen=screen_reclaims(self.b);events={};links={}
        for i in sorted(self.valid):
            if not screen[i]:continue
            result=self.get(i)
            if result is None or result[0]['status']!='SETUP':continue
            p,b,c=result;p=self.decorated(p,b,c,'strategy');events[p['id']]=p
            sign=1 if int(digest(p['id']+':direction')[:16],16)<2**63 else -1
            distance=abs(p['reference_entry']-p['stop'])
            control=dict(p,id=digest(p['id']+':direction'),arm='random_direction',direction='LONG' if sign==1 else 'SHORT',
                         stop=p['reference_entry']-sign*distance,target=p['reference_entry']+sign*2*distance,
                         entry_limit=p['reference_entry']+sign*CFG.max_chase_atr*p['atr'])
            events[control['id']]=control
            timing=self.timing(p,i)
            if timing:events[timing['id']]=timing
            links[p['id']]=dict(random_direction=control['id'],random_timing=timing['id'] if timing else None)
        return dict(symbol=self.symbol,events=list(events.values()),links=links,counts=dict(self.counts),
                    source_coverage='Archive/metadata limitations are in universe.json; no current listing filter')


def discover_symbol(args):
    cache,output,symbol=args;path=Path(output)/'candidates'/(symbol+'.json.gz')
    if path.exists():return symbol,'CACHED'
    b,bi=read_rows(cache,symbol,'30m',MONTHS);c,ci=read_rows(cache,symbol,'4h',MONTHS)
    btc,_=read_rows(cache,'BTCUSDT','4h',MONTHS)
    if not len(b) or not len(c):result=dict(symbol=symbol,events=[],links={},counts={'no_coarse_data':1})
    else:result=Discovery(symbol,b,c,btc).run()
    result['data_issues']=bi+ci
    path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix('.tmp')
    with gzip.open(tmp,'wt',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,allow_nan=False,separators=(',',':'))
    tmp.replace(path)
    return symbol,len(result['links'])


class MinutePath:
    """Dense numeric storage with exact timestamp checks, including gaps."""
    def __init__(self,rows):self.rows=rows;self.times=rows[:,0];self.first=int(rows[0,0]) if len(rows) else 0
    def index(self,t):
        i=(int(t)-self.first)//MINUTE
        if 0<=i<len(self.rows) and self.times[i]==t:return i
        i=int(np.searchsorted(self.times,t))
        return i if i<len(self.rows) and self.times[i]==t else None
    def __contains__(self,t):return self.index(t) is not None
    def __getitem__(self,t):
        i=self.index(t)
        if i is None:raise KeyError(t)
        row=self.rows[i].tolist();row[0]=int(row[0]);row[6]=int(row[6]);return row
    def get(self,t,default=None):return self[t] if t in self else default
