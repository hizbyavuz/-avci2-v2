"""Exploratory archive replay. Execution assumptions are explicit, never live proof."""
import argparse
from bisect import bisect_left,bisect_right
from collections import Counter,defaultdict
from dataclasses import replace
from datetime import datetime,timezone
import gzip
import hashlib
import json
from pathlib import Path
import random
import sqlite3
import time

from .archive import Archive,ArchiveMissing
from .core import CFG,MINUTE,assess,closed_bars,atr,ema,sizing,exit_on_bar,funding_cost,risk_room
from .observations import context_fields,HORIZON
from .provenance import identity

DAY=1440*MINUTE
SEED='ta-paper-v1-1-20261026-fixed'
SPREAD=.001
PROFILES={'base':CFG,'cost_1_5':replace(CFG,fee_each_side=.00075,entry_slippage=.0003,target_slippage=.0003,stop_slippage=.0015),
          'cost_2':replace(CFG,fee_each_side=.001,entry_slippage=.0004,target_slippage=.0004,stop_slippage=.002),
          'stop_1pct':replace(CFG,stop_slippage=.01)}


def ms(iso):return int(datetime.fromisoformat(iso.replace('Z','+00:00')).timestamp()*1000)
def draw(key):return int(hashlib.sha256((SEED+':'+key).encode()).hexdigest()[:16],16)/2**64

def market(symbol,price):
    # Historical exchange filters/order book are unavailable. Do not use today's
    # metadata or imply an exact executable reconstruction. Near-continuous units.
    return {'symbol':symbol,'filters':[{'filterType':'PRICE_FILTER','tickSize':str(price*1e-9)},
        {'filterType':'LOT_SIZE','stepSize':'0.00000001','minQty':'0.00000001','maxQty':'1e15'},
        {'filterType':'MIN_NOTIONAL','notional':'5'}]}


def window(rows,times,decision,period):
    # Match the live latest-120 response: 119 closed bars plus the open bar
    # that is excluded. Preserve core.py and its EMA seed/strategy rules.
    last=bisect_right(times,decision-CFG.publication_delay_ms-period)
    return rows[max(0,last-(CFG.history_bars-1)):last]


def controls(p,bars):
    result=[]
    key=f"{p['symbol']}:{p['candle_ms']}"
    if p['status']=='SETUP':
        sign=1 if draw(key+':direction')<.5 else -1
        distance=abs(p['reference_entry']-p['stop'])
        result.append(dict(p,arm='random_direction',direction='LONG' if sign==1 else 'SHORT',
            stop=p['reference_entry']-sign*distance,target=p['reference_entry']+sign*2*distance,
            entry_limit=p['reference_entry']+sign*CFG.max_chase_atr*p['atr']))
    # Fixed random timing, independent of the reclaim condition/outcome.
    if p.get('trend') and draw(key+':timing')<.05:
        sign=p['trend'];a=p['atr'];last=bars[-1];tail=bars[-CFG.pullback_bars:]
        stop=min(r[3] for r in tail)-.1*a if sign==1 else max(r[2] for r in tail)+.1*a
        distance=sign*(last[4]-stop)
        if a>0 and last[2]-last[3]<=1.5*a and .004<=distance/last[4]<=.05:
            now=p['observed_ms']
            result.append(dict(p,status='SETUP',arm='random_timing',stop=stop,target=last[4]+sign*2*distance,
                entry_limit=last[4]+sign*.25*a,entry_ms=((now+CFG.entry_delay_ms)//MINUTE+1)*MINUTE,
                expires_ms=now+CFG.max_pending_ms))
    return result


def candidates(ar,symbol,start,end):
    b=ar.rows(symbol,'30m',['2026-07','2026-08'],optional=True)
    c=ar.rows(symbol,'4h',['2026-07','2026-08'],optional=True)
    bt=[r[0] for r in b];ct=[r[0] for r in c]
    rows=[];counts=Counter();coverage=Counter()
    if not b or not c:return [],dict(missing_coarse=1),{}
    for t in range(start,end,CFG.entry_ms):
        now=t+CFG.entry_ms+60_000
        # Do not treat an inferred archive first print as a verified listing date.
        if now-b[0][0]<CFG.min_listing_days*DAY:
            counts['AGE_NOT_ESTABLISHED']=counts['AGE_NOT_ESTABLISHED']+1;continue
        bars=window(b,bt,now,CFG.entry_ms);context=window(c,ct,now,CFG.context_ms)
        if len(bars)<48 or sum(r[7] for r in bars[-48:])<CFG.min_quote_volume:
            counts['LOW_VOLUME_OR_SHORT_HISTORY']+=1;continue
        coverage[str(t)]+=1
        try:
            p=assess(symbol,bars,context,now)
            counts[p['reason']]+=1
            peers=controls(p,bars)
            if p['status']=='SETUP':peers.insert(0,dict(p,arm='strategy'))
            for x in peers:
                x.update(context_fields(x,bars,context),quote_volume=sum(r[7] for r in bars[-48:]))
                x['id']=hashlib.sha256(f"{x['arm']}:{symbol}:{t}".encode()).hexdigest()
                rows.append(x)
        except (ValueError,IndexError,KeyError) as e:
            counts['DATA:'+str(e)]+=1
            coverage['invalid:'+str(t)]+=1
    return rows,dict(counts),dict(coverage)


def funding_evidence(symbol,rates,marks,start,end):
    """Missing archives/intervals are unknown, not zero funding."""
    if not rates:raise ValueError('FUNDING_ARCHIVE_MISSING')
    rt=[r[0] for r in rates]
    left=bisect_left(rt,start);right=bisect_right(rt,end)
    # Bracketing records certify that an apparent empty interval isn't missing.
    if left==0 or right==len(rates):raise ValueError('FUNDING_NOT_BRACKETED')
    for i in range(left,right+1):
        interval=rates[i][2] if len(rates[i])>2 else 8
        if rates[i][0]-rates[i-1][0]>interval*60*MINUTE+MINUTE:
            raise ValueError('FUNDING_INTERVAL_GAP')
    result=[]
    for t,rate,*_ in rates[left:right]:
        anchor=t//CFG.entry_ms*CFG.entry_ms
        if t-anchor>1000 or anchor not in marks:raise ValueError('FUNDING_MARK_PRICE_MISSING')
        result.append(dict(symbol=symbol,fundingTime=t,fundingRate=rate,markPrice=marks[anchor][1]))
    return result


def execute(p,path,rates,marks,cfg=CFG,multiplier=1):
    """Same pure sizing/exit functions, fixed latency, no invented missing fills."""
    p=dict(p);sign=1 if p['direction']=='LONG' else -1
    p['entry_half_spread']=SPREAD*multiplier/2
    m=market(p['symbol'],p['reference_entry'])
    stamp=p['observed_ms']//MINUTE*MINUTE
    base=dict(id=p['id'],arm=p['arm'],symbol=p['symbol'],direction=p['direction'],
        observed_ms=p['observed_ms'],entry_ms=p['entry_ms'],candle_ms=p['candle_ms'],
        quote_volume=p['quote_volume'],extension_4h_atr=p.get('extension_4h_atr'),
        room_60_bars_r=p.get('room_60_bars_r'),btc_agreement=p.get('btc_agreement'),
        status='ADMISSION_REJECTED',marks=[])
    try:
        initial=sizing(p,m,1000,10,cfg=cfg)
        if stamp not in path:return dict(base,status='UNRESOLVED_DATA',reason='NO_DECISION_PRICE')
        quote=path[stamp][1]*(1+sign*p['entry_half_spread'])
        sizing(p,m,1000,10,quote,cfg,reference_is_quote=True)
    except ValueError as e:return dict(base,reason=str(e))
    base.update(status='PENDING',initial_loss=initial['planned_loss'])
    for t in range(stamp,p['entry_ms'],MINUTE):
        if t not in path:return dict(base,status='UNRESOLVED_DATA',reason='PRE_ENTRY_PATH_GAP')
        b=path[t]
        if (b[3]<=initial['stop'] or b[2]>=initial['target']) if sign==1 else (b[2]>=initial['stop'] or b[3]<=initial['target']):
            return dict(base,status='CANCELLED',reason='PRE_ENTRY_LEVEL',known_ms=t+MINUTE+CFG.publication_delay_ms)
    if p['entry_ms'] not in path:return dict(base,status='UNRESOLVED_DATA',reason='ENTRY_PATH_GAP')
    try:
        # Keep quantized levels from the published plan, as the live worker does.
        p.update(stop=initial['stop'],target=initial['target'])
        fill=sizing(p,m,1000,initial['planned_loss'],path[p['entry_ms']][1],cfg)
        if fill['quantity']>initial['quantity']:
            ratio=initial['quantity']/fill['quantity']
            for k in ('quantity','notional','planned_loss','stress_loss','target_net','indicative_margin'):fill[k]*=ratio
    except ValueError as e:return dict(base,status='CANCELLED',reason=str(e),known_ms=p['entry_ms']+MINUTE+CFG.publication_delay_ms)
    p.update(fill);base.update(status='OPEN_AT_HORIZON',entry=p['entry'],stop=p['stop'],target=p['target'],
        quantity=p['quantity'],planned_loss=p['planned_loss'],notional=p['notional'],net_rr=p['net_rr'])
    for t in range(p['entry_ms'],p['entry_ms']+HORIZON,MINUTE):
        if t not in path:return dict(base,status='UNRESOLVED_DATA',reason='OUTCOME_PATH_GAP',first_missing_ms=t)
        b=path[t]
        # Previous closed minute mark at each portfolio decision boundary.
        if (t+MINUTE)%CFG.entry_ms==MINUTE:
            base['marks'].append([t+MINUTE,min(0,sign*p['quantity']*(b[4]-p['entry']))])
        result=exit_on_bar(p,b,cfg)
        if result:
            base.update(result,status='FUNDING_UNRESOLVED',known_ms=b[6]+2*MINUTE+CFG.publication_delay_ms)
            try:
                fund=funding_evidence(p['symbol'],rates,marks,p['entry_ms'],b[6])
                cost,uncertain=funding_cost(p,fund,b[6])
                base.update(status='CLOSED',funding_cost=cost,funding_boundary_uncertain=uncertain,
                    net_pnl=result['pnl_before_funding']-cost,
                    net_r=(result['pnl_before_funding']-cost)/p['planned_loss'],
                    stress_net_r=(result['stress_pnl_before_funding']-cost)/p['planned_loss'])
            except ValueError as e:base['funding_issue']=str(e)
            return base
    return base


def portfolio(events,cfg=CFG):
    """Chronological capacity/risk approximation with causal archived marks.

    Archive filters, network latencies and order books remain assumptions. No
    automatic 72h liquidation: a censored holding keeps its slot occupied.
    """
    accepted=[];active=[];rejections=Counter();last={};equity=cfg.paper_initial_equity;closed=[]
    def settle(now):
        nonlocal equity,active
        keep=[]
        for p in active:
            if now>=p['entry_ms']+MINUTE+CFG.publication_delay_ms and p.get('actual_loss') is not None:
                p['planned_loss']=p['actual_loss']
            if p['status'] in ('CLOSED','CANCELLED') and p['known_ms']<=now:
                if p['status']=='CLOSED':equity+=p['net_pnl'];closed.append(p)
            else:keep.append(p)
        active=keep
    for event in sorted(events,key=lambda p:(p['observed_ms'],-p['quote_volume'],p['symbol'])):
        now=event['observed_ms'];settle(now)
        if event['status']=='ADMISSION_REJECTED':rejections[event.get('reason','REJECTED')]+=1;continue
        if now-last.get(event['symbol'],-10**20)<CFG.cooldown_ms:
            rejections['SYMBOL_COOLDOWN']+=1;continue
        if any(p['status'] in ('UNRESOLVED_DATA','FUNDING_UNRESOLVED') and now>=p.get('first_missing_ms',p.get('exit_interval_end',p['entry_ms'])) for p in active):
            rejections['UNRESOLVED_OPEN_RISK']+=1;continue
        daily=sum(p['net_pnl'] for p in closed if p['exit_interval_end']//DAY==now//DAY)
        unreal=0
        for p in active:
            values=[x[1] for x in p.get('marks',[]) if x[0]<=now]
            if values:unreal+=values[-1]*p['equity_scale']
        eq=equity+unreal
        try:room=risk_room(eq,equity-daily,daily,active,event['direction'],cfg)
        except ValueError as e:rejections[str(e)]+=1;continue
        if event['status']=='UNRESOLVED_DATA' and 'initial_loss' not in event:
            rejections['UNVERIFIED_ADMISSION_DATA']+=1;continue
        initial_loss=event.get('initial_loss',cfg.paper_initial_equity*cfg.risk_fraction)
        scale=min(eq/1000,room/initial_loss)
        if scale<=0:rejections['NO_RISK_ROOM']+=1;continue
        p=dict(event,equity_scale=scale,actual_loss=event.get('planned_loss',initial_loss)*scale,planned_loss=initial_loss*scale)
        if 'net_pnl' in p:p['net_pnl']*=scale
        # Same near-continuous quantity approximation; no exact lot-size claim.
        accepted.append(p);active.append(p);last[p['symbol']]=now
    settle(10**20)
    return accepted,dict(rejections),equity


def bootstrap(rows,block_days,iterations=2000):
    blocks=defaultdict(list)
    for p in rows:blocks[p['entry_ms']//(block_days*DAY)].append(p['net_r'])
    aggregates=[(sum(v),len(v)) for v in blocks.values()]
    if len(aggregates)<2:return None
    rng=random.Random(SEED+str(block_days));values=[]
    for _ in range(iterations):
        sampled=[rng.choice(aggregates) for _ in aggregates]
        values.append(sum(x[0] for x in sampled)/sum(x[1] for x in sampled))
    values.sort()
    return {'lo90':values[int(iterations*.05)],'hi90':values[int(iterations*.95)],'blocks':len(blocks),
            'warning':'Few calendar blocks; exploratory interval, not independent trade count.'}


def stats(events):
    closed=[p for p in events if p['status']=='CLOSED'];states=Counter(p['status'] for p in events)
    def mean(v):return sum(x['net_r'] for x in v)/len(v) if v else None
    coin=defaultdict(float);days=defaultdict(float)
    for p in closed:coin[p['symbol']]+=p['net_r'];days[p['entry_ms']//DAY]+=p['net_r']
    top=set(sorted(coin,key=coin.get,reverse=True)[:3]);best=set(sorted(days,key=days.get,reverse=True)[:5])
    return dict(status_counts=dict(states),n_closed=len(closed),n_entry_days=len(days),mean_net_r=mean(closed),
        win_rate=sum(p['net_r']>0 for p in closed)/len(closed) if closed else None,
        mean_funding_usdt=sum(p['funding_cost'] for p in closed)/len(closed) if closed else None,
        day_block_ci90=bootstrap(closed,1),week_block_ci90=bootstrap(closed,7),
        without_top3_coins_mean_r=mean([p for p in closed if p['symbol'] not in top]),removed_top3=sorted(top),
        without_best5_days_mean_r=mean([p for p in closed if p['entry_ms']//DAY not in best]),removed_best5=sorted(best),
        btc_comparator_count=sum('coin_net_minus_gross_btc' in p for p in closed),
        mean_coin_net_minus_gross_btc=(sum(p['coin_net_minus_gross_btc'] for p in closed if 'coin_net_minus_gross_btc' in p)/sum('coin_net_minus_gross_btc' in p for p in closed) if any('coin_net_minus_gross_btc' in p for p in closed) else None),
        btc_comparator_note='Directional gross BTC return over same entry/exit-minute window; no beta or volatility matching, not an alpha estimate.',
        incomplete_outcomes=states['UNRESOLVED_DATA']+states['FUNDING_UNRESOLVED']+states['OPEN_AT_HORIZON'])


def summarize(events):
    report={}
    for profile in PROFILES:
        report[profile]={}
        for arm in ('strategy','random_direction','random_timing'):
            sample=[p for p in events if p['profile']==profile and p['arm']==arm]
            portfolio_sample,rejections,equity=portfolio(sample,PROFILES[profile])
            report[profile][arm]={mode:{'all':stats(rows),'long':stats([p for p in rows if p['direction']=='LONG']),
                'short':stats([p for p in rows if p['direction']=='SHORT'])} for mode,rows in
                [('all_setups',sample),('portfolio_approximation',portfolio_sample)]}
            report[profile][arm]['portfolio_admission_rejections']=rejections
            report[profile][arm]['portfolio_realized_equity']=equity
    return report


def difference_ci(a,b,block_days=1,iterations=2000):
    """Resample shared calendar blocks, preserving within-block dependence."""
    groups=[]
    for rows in (a,b):
        g=defaultdict(lambda:[0.,0])
        for day,total,n in rows:
            g[day//block_days][0]+=total;g[day//block_days][1]+=n
        groups.append(g)
    keys=sorted(set(groups[0])|set(groups[1]))
    if len(keys)<2 or not a or not b:return None
    rng=random.Random(SEED+':difference:'+str(block_days));values=[]
    for _ in range(iterations):
        picked=[rng.choice(keys) for _ in keys];sums=[]
        for g in groups:
            total=sum(g.get(k,[0,0])[0] for k in picked);n=sum(g.get(k,[0,0])[1] for k in picked)
            sums.append(total/n if n else None)
        if None not in sums:values.append(sums[0]-sums[1])
    if not values:return None
    values.sort();n=len(values)
    return {'mean_difference_r':sum(x[1] for x in a)/sum(x[2] for x in a)-sum(x[1] for x in b)/sum(x[2] for x in b),
        'lo90':values[int(n*.05)],'hi90':values[min(n-1,int(n*.95))],'blocks':len(keys),
        'conditional_on_resolved_and_filled':True}


def summarize_database(db):
    """Load one arm/cost group at a time; full-universe evidence stays on disk."""
    report={}
    for profile in PROFILES:
        report[profile]={}
        for arm in ('strategy','random_direction','random_timing'):
            sample=[json.loads(r[0]) for r in db.execute('SELECT payload FROM outcomes WHERE profile=? AND arm=?',(profile,arm))]
            limited,rejections,equity=portfolio(sample,PROFILES[profile])
            report[profile][arm]={mode:{'all':stats(rows),'long':stats([p for p in rows if p['direction']=='LONG']),
                'short':stats([p for p in rows if p['direction']=='SHORT'])} for mode,rows in [('all_setups',sample),('portfolio_approximation',limited)]}
            report[profile][arm]['portfolio_admission_rejections']=rejections
            report[profile][arm]['portfolio_realized_equity']=equity
        comparisons={}
        for direction in ('LONG','SHORT','ALL'):
            def daily(arm):
                rows=db.execute("SELECT CAST(json_extract(payload,'$.entry_ms') / ? AS INTEGER), SUM(json_extract(payload,'$.net_r')), COUNT(*) FROM outcomes WHERE profile=? AND arm=? AND json_extract(payload,'$.status')='CLOSED' AND (?='ALL' OR json_extract(payload,'$.direction')=?) GROUP BY 1",(DAY,profile,arm,direction,direction)).fetchall()
                return rows
            a=daily('strategy')
            comparisons[direction]={control:{'day':difference_ci(a,daily(control),1),'week':difference_ci(a,daily(control),7)} for control in ('random_direction','random_timing')}
        report[profile]['control_comparisons_all_setups']=comparisons
    return report


def symbol_replay(ar,symbol,start,end,provenance,btc_context=None,btc_path=None):
    plans,counts,coverage=candidates(ar,symbol,start,end)
    if not plans:return {'symbol':symbol,'assessment_counts':counts,'coverage':coverage,'events':[]}
    path={r[0]:r[:7] for r in ar.rows(symbol,'1m',['2026-08','2026-09'],optional=True)}
    rates=ar.rows(symbol,'1m',['2026-08','2026-09'],kind='fundingRate',optional=True)
    marks={r[0]:r for r in ar.rows(symbol,'30m',['2026-08','2026-09'],kind='markPriceKlines',optional=True)}
    events=[]
    for p in plans:
        trend=(btc_context or {}).get(p['observed_ms'])
        sign=1 if p['direction']=='LONG' else -1
        p['btc_agreement']=None if trend is None else 'NEUTRAL' if trend==0 else 'SAME' if sign==trend else 'OPPOSITE'
        for profile,cfg in PROFILES.items():
            mult={'cost_1_5':1.5,'cost_2':2}.get(profile,1)
            result=execute(p,path,rates,marks,cfg,mult)
            result.update(profile=profile)
            if result['status']=='CLOSED' and btc_path:
                bt0=btc_path.get(result['entry_ms']);bt1=btc_path.get(result['exit_interval_start'])
                if bt0 and bt1:
                    result['gross_directional_btc_return']=sign*(bt1[4]/bt0[1]-1)
                    result['coin_net_return']=result['net_pnl']/result['notional']
                    result['coin_net_minus_gross_btc']=result['coin_net_return']-result['gross_directional_btc_return']
            events.append(result)
    return {'symbol':symbol,'provenance':provenance,'assessment_counts':counts,'coverage':coverage,'events':events}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache',required=True);parser.add_argument('--output',required=True)
    parser.add_argument('--workers',type=int,default=2);parser.add_argument('--wait-for-coarse',action='store_true')
    parser.add_argument('--trim-minute-cache',action='store_true')
    args=parser.parse_args()
    if not 1<=args.workers<=4:raise ValueError('workers must be 1..4')
    from concurrent.futures import ThreadPoolExecutor,as_completed
    ar=Archive(args.cache);out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    chunks=out/'symbols';chunks.mkdir(exist_ok=True)
    provenance=identity();start=ms('2026-08-01T00:00:00Z');end=ms('2026-09-01T00:00:00Z')
    manifest_path=out/'run_identity.json'
    protocol_path=Path(__file__).resolve().parent.parent/'research/ta_paper_v1_1/protocol.json'
    protocol=json.loads(protocol_path.read_text())
    run_id={'protocol_sha256':hashlib.sha256(protocol_path.read_bytes()).hexdigest(),'source_sha256':provenance['source_sha256'],'config_hash':CFG.fingerprint,'seed':SEED}
    if manifest_path.exists() and json.loads(manifest_path.read_text())!=run_id:
        raise RuntimeError('REPLAY_CODE_CHANGED_USE_NEW_OUTPUT_DIRECTORY')
    manifest_path.write_text(json.dumps(run_id,indent=2)+'\n')
    completed=set(p.stem.removesuffix('.json') for p in chunks.glob('*.json.gz'))
    errors={};finished=False
    btc_rows=ar.rows('BTCUSDT','4h',['2026-07','2026-08'],optional=True)
    btc_times=[r[0] for r in btc_rows];btc_context={}
    for t in range(start,end,CFG.entry_ms):
        now=t+CFG.entry_ms+60_000
        try:
            c=closed_bars(window(btc_rows,btc_times,now,CFG.context_ms),CFG.context_ms,now)
            f=ema([r[4] for r in c],20);slow=ema([r[4] for r in c],50)
            btc_context[now]=1 if c[-1][4]>f[-1]>slow[-1] and f[-1]>f[-4] else -1 if c[-1][4]<f[-1]<slow[-1] and f[-1]<f[-4] else 0
        except ValueError:pass
    btc_path={r[0]:r for r in ar.rows('BTCUSDT','1m',['2026-08','2026-09'],optional=True)}
    while True:
        manifest=json.loads((ar.cache/'coarse_manifest.json').read_text())
        ready=sorted(set(manifest['ready'])-completed-set(errors))
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures={pool.submit(symbol_replay,ar,s,start,end,provenance,btc_context,btc_path):s for s in ready}
            for f in as_completed(futures):
                symbol=futures[f]
                try:
                    result=f.result()
                    with gzip.open(chunks/(symbol+'.json.gz'),'wt',encoding='utf-8') as g:json.dump(result,g,allow_nan=False,separators=(',',':'))
                    completed.add(symbol)
                    if args.trim_minute_cache:
                        for month in ('2026-08','2026-09'):
                            cached=ar.cache/ar.key(symbol,'1m',month)
                            if cached.exists():cached.unlink()  # Reproducible cache only; checksum retained.
                    print(json.dumps({'symbol':symbol,'completed':len(completed),'events':len(result['events'])}),flush=True)
                except Exception as exc:
                    errors[symbol]=type(exc).__name__+':'+str(exc)[:120]
                    print(json.dumps({'symbol':symbol,'error':errors[symbol]}),flush=True)
                (out/'progress.json').write_text(json.dumps({'completed':sorted(completed),'errors':errors,'provenance':provenance},indent=2)+'\n')
        finished=len(manifest['ready'])+len(manifest['missing'])+len(manifest['errors'])+len(manifest['excluded'])==manifest['catalog_count']
        if not args.wait_for_coarse or finished or ar.blocked:break
        time.sleep(15)
    db=sqlite3.connect(out/'replay_results.sqlite3')
    db.execute('CREATE TABLE IF NOT EXISTS outcomes(profile TEXT,arm TEXT,id TEXT,payload TEXT,PRIMARY KEY(profile,arm,id))')
    assessed=Counter();coverage=Counter();event_count=0
    for p in sorted(chunks.glob('*.json.gz')):
        with gzip.open(p,'rt') as g:chunk=json.load(g)
        db.executemany('INSERT OR REPLACE INTO outcomes VALUES(?,?,?,?)',
            ((e['profile'],e['arm'],e['id'],json.dumps(e,separators=(',',':'))) for e in chunk['events']))
        db.commit();event_count+=len(chunk['events'])
        assessed.update(chunk['assessment_counts']);coverage.update(chunk['coverage'])
    report={'classification':'EXPLORATORY_NOT_HOLDOUT','version':CFG.version,'config_hash':CFG.fingerprint,
        'provenance':provenance,'protocol':protocol,'run_identity':run_id,'universe_manifest':manifest,'completed_symbols':len(completed),'errors':errors,
        'full_coarse_discovery_complete':finished,'assessment_counts':dict(assessed),'coverage':dict(coverage),
        'assumptions':{'spread_fraction':SPREAD,'latency_seconds':60,'funding_mark':'30m mark candle OPEN at scheduled funding, <=1s timestamp offset',
            'historical_filters':'near-continuous tick/step, min notional 5 USDT; unverified historical exchange rules',
            'universe':'archive names; first available July bar is a conservative age proxy, not historical listing metadata',
            '72h':'right censoring, NOT an exit or a zero/loss assignment',
            'portfolio':'chronological risk/capacity approximation; not identical live order book/lot/latency replay'},
        'results':summarize_database(db)}
    (out/'report.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    hashes={str(p.relative_to(ar.cache)):p.read_text().strip() for p in sorted(ar.cache.rglob('*.sha256'))}
    (out/'archive_checksums.json').write_text(json.dumps(hashes,indent=2)+'\n')
    print(json.dumps({'report':str(out/'report.json'),'events':event_count,'completed_symbols':len(completed),'errors':errors}),flush=True)

if __name__=='__main__':main()
