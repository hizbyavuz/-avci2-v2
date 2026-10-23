"""Read existing collectors; append separate, non-executing old/new evidence.

Full prefixes have a frozen starting candle. They are never silently slid or
reseeded. A resource/prefix limit pauses this observer, not the production motor.
"""
import argparse
from collections import Counter
import fcntl
import json
import os
from pathlib import Path
import signal
import sqlite3
import threading
import time

from structure_engine.config import CONFIG as OLD_CONFIG
from structure_engine.core import assess
from structure_engine.runtime import SOURCES, read_frames
from . import observe
from .engine import PERIODS, _validate
from .records import build_record, capture_price_filter, digest, canonical
from .shadow_config import VERSION, OPS, OPS_HASH
from . import shadow_store as store

FIELDS = 'open_ms,close_ms,open,high,low,close,quote_volume,taker_buy_quote,source,received_ms'
VALUES = ('open_ms','close_ms','open','high','low','close','quote_volume','taker_buy_quote')


def readonly(path):
    path = Path(path)
    if not path.is_file(): raise ValueError('COLLECTOR_DB_MISSING')
    c = sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)
    c.row_factory = sqlite3.Row; c.execute('PRAGMA query_only=ON'); c.execute('PRAGMA cache_size=-2048')
    return c


def tick_catalogue(metadata,now):
    row = metadata.execute('SELECT observed_ms,payload FROM market_metadata WHERE id=1').fetchone()
    if not row or not 0 <= now-row['observed_ms'] <= OPS['metadata_max_age_ms']:
        raise ValueError('PRICE_FILTER_CATALOGUE_MISSING_OR_STALE')
    payload = json.loads(row['payload'])
    if payload.get('source')!='BINANCE_UM_FUTURES_REST': raise ValueError('PRICE_FILTER_CATALOGUE_SOURCE')
    markets = {m['symbol']:m for m in payload['markets']}
    return row['observed_ms'],markets,digest(payload)


def tick_for(catalogue,symbol):
    if catalogue is None or symbol not in catalogue[1]: return None
    observed,markets,source_hash = catalogue
    try:
        snapshot = capture_price_filter({'symbols':[markets[symbol]]},symbol,observed)
        snapshot.update(response_scope='COLLECTOR_PRESERVED_MARKET_ROW',catalogue_sha256=source_hash,
                        collector_source='BINANCE_UM_FUTURES_REST')
        return snapshot
    except (KeyError,TypeError,ValueError):
        return None


def append_prefix(c,source,symbol,tf,now):
    quarantined = c.execute('SELECT reason FROM quarantine WHERE symbol=? AND tf=?',(symbol,tf)).fetchone()
    if quarantined: raise ValueError('PREFIX_QUARANTINED:'+tf+':'+quarantined[0])
    anchor = c.execute('SELECT start_ms FROM streams WHERE symbol=? AND tf=?',(symbol,tf)).fetchone()
    stored = c.execute('SELECT open_ms,payload,value_hash FROM bars WHERE symbol=? AND tf=? ORDER BY open_ms',
                       (symbol,tf)).fetchall()
    if not anchor:
        found = source.execute('SELECT '+FIELDS+''' FROM futures_closed_klines
            WHERE symbol=? AND interval=? AND close_ms<? AND received_ms<=?
            ORDER BY open_ms DESC LIMIT ?''',(symbol,tf,now,now,OPS['seed_bars'])).fetchall()
        rows = [dict(b) for b in reversed(found)]
        if len(rows)<OPS['minimum_seed_bars']: raise ValueError('PREFIX_WARMUP:'+tf)
    else:
        start = stored[max(0,len(stored)-OPS['overlap_bars'])]['open_ms']
        rows = [dict(b) for b in source.execute('SELECT '+FIELDS+''' FROM futures_closed_klines
            WHERE symbol=? AND interval=? AND open_ms>=? AND close_ms<? AND received_ms<=?
            ORDER BY open_ms LIMIT ?''',(symbol,tf,start,now,now,OPS['fetch_batch_bars'])).fetchall()]
    if any(b['source'] not in SOURCES for b in rows): raise ValueError('PREFIX_UNVERIFIED_SOURCE:'+tf)
    prior = {b['open_ms']:b for b in stored}
    fresh = []
    for b in rows:
        value_hash = digest({k:b[k] for k in VALUES})
        if b['open_ms'] in prior:
            if value_hash != prior[b['open_ms']]['value_hash']:
                c.execute('INSERT OR IGNORE INTO quarantine VALUES(?,?,?,?,?)',
                          (symbol,tf,'SOURCE_CANDLE_CHANGED',now,canonical(dict(open_ms=b['open_ms']))))
                c.commit(); raise ValueError('PREFIX_SOURCE_REVISION:'+tf)
        else:
            fresh.append((b,value_hash))
    if len(stored)+len(fresh)>OPS['max_prefix_bars']: raise ValueError('PREFIX_LIMIT_REACHED_NO_RESEED:'+tf)
    previous = [store.unpack(b['payload']) for b in stored]
    combined = previous+[b for b,_ in fresh]
    errors = [e for e in _validate(combined,tf,now) if not e.startswith('STALE_HISTORY:')]
    if errors: raise ValueError('PREFIX_INVALID:'+','.join(errors))
    with c:
        if not anchor:
            c.execute('INSERT INTO streams VALUES(?,?,?)',(symbol,tf,combined[0]['open_ms']))
        for b,h in fresh:
            c.execute('INSERT INTO bars VALUES(?,?,?,?,?,?)',(symbol,tf,b['open_ms'],b['close_ms'],h,store.pack(b)))
    return combined


def old_summary(old):
    fields = ('version','config_hash','symbol','interval','asof_ms','setup_close_ms','status','reason','data_errors','decisions')
    result = {k:old.get(k) for k in fields}
    result['context_directions'] = {tf:v['direction'] for tf,v in old.get('context',{}).items()}
    result['recomputed_comparison_not_delivery_receipt'] = True
    return result


def new_summary(new):
    if new is None: return None
    fields = ('version','config_hash','input_hash','asof_ms','mode','decision_effect','status','reason','issues','late')
    result = {k:new.get(k) for k in fields}
    result['context'] = {tf:dict(structure=v['structure'],atr=v['atr'],bars=v['bars'],
                              zone_window_complete=v['zone_window_complete']) for tf,v in new.get('contexts',{}).items()}
    result['setup_ids'] = [s['id'] for s in new['setups']]
    return result


def scan_symbol(c,source,catalogue,symbol,now,directory):
    old_frames = read_frames(source,symbol,'15m',now)
    bar_ms = old_frames['15m'][-1]['close_ms'] if old_frames['15m'] else None
    if bar_ms and c.execute('SELECT 1 FROM scans WHERE symbol=? AND bar_ms=? AND pair_ready=1 LIMIT 1',
                            (symbol,bar_ms)).fetchone():
        return dict(state='ALREADY_SCANNED',setups=0)
    old = assess(symbol,old_frames,now,'15m')
    frames = {}; prefix_errors = []
    for tf in PERIODS:
        try: frames[tf] = append_prefix(c,source,symbol,tf,now)
        except ValueError as exc: prefix_errors.append(str(exc))
    current_catalogue = (catalogue if catalogue is not None and
                         0 <= now-catalogue[0] <= OPS['metadata_max_age_ms'] else None)
    tick = tick_for(current_catalogue,symbol)
    new = None
    if not prefix_errors:
        new = observe(symbol,frames,now,provenance=dict(kind='LIVE_COLLECTOR_CLOSED_PREFIX',
            collector_sources=sorted(SOURCES),fixed_prefix=True,source_db_read_only=True,
            observed_ms=now,source_hash=digest(frames)))
    key = digest([VERSION,symbol,bar_ms,digest(old_frames),
                  new.get('input_hash') if new else prefix_errors,tick])
    if c.execute('SELECT 1 FROM scans WHERE id=?',(key,)).fetchone():
        return dict(state='UNCHANGED_DATA_ISSUE',setups=0)
    records = []
    for s in new['setups'] if new else []:
        record = build_record(new,s['id'],frames,tick)
        path = store.write_blob(directory,record)
        records.append(dict(record_hash=record['record_hash'],setup_id=s['id'],evidence=path,
                            side=s['direction'],state=record['display']['state'],
                            grid_status=record['grid']['status'],levels=record['display']['levels']))
    from .records import price_grid
    grid_ok = price_grid(tick,symbol,now)['status']=='SUPPLIED_PRICE_FILTER'
    ready = bool(new and new['status']=='OBSERVED' and old['status'] not in ('DATA_INVALID','ILLIQUID') and grid_ok)
    activation = c.execute('SELECT started_ms FROM activation WHERE id=1').fetchone()[0]
    cohort = 'FORWARD' if bar_ms and bar_ms>=activation else 'STARTUP_REPLAY'
    group_id = None
    if old['decisions'] or records:
        group_id,cohort = store.assign_group(c,symbol,now,bar_ms or 0,key,ready)
    pair = dict(version=VERSION,ops_hash=OPS_HASH,symbol=symbol,bar_ms=bar_ms,observed_ms=now,
        old=old_summary(old),new=new_summary(new),new_records=records,prefix_errors=prefix_errors,
        metadata_verified_at_observation=grid_ok,cohort=cohort,pair_ready=ready,group_id=group_id,
        old_input_hash=digest(old_frames),old_frames=old_frames if old['decisions'] else None,
        new_prefixes={tf:dict(start_ms=rows[0]['open_ms'],end_ms=rows[-1]['close_ms'],bars=len(rows),
                             input_hash=digest(rows)) for tf,rows in frames.items()},
        decision_effect='NONE',real_orders=False)
    path = store.write_blob(directory,pair)
    c.execute('INSERT INTO scans VALUES(?,?,?,?,?,?,?,?,?)',
              (key,symbol,bar_ms,now,cohort,int(ready),group_id,path,store.pack(dict(
                  old_status=old['status'],old_setups=len(old['decisions']),new_status=new['status'] if new else 'PREFIX_NOT_READY',
                  new_setups=len(records),prefix_errors=prefix_errors,grid_ok=grid_ok))))
    for r in records:
        c.execute('INSERT OR IGNORE INTO setups VALUES(?,?,?,?,?,?,?,?)',
                  (r['record_hash'],key,symbol,r['side'],now,r['record_hash'],r['evidence'],store.pack(r)))
    c.commit()
    return dict(state='PAIRED' if ready else 'RECORDED_WITH_DATA_ISSUE',setups=len(records),
                prefix_errors=prefix_errors,grid_ok=grid_ok)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-db',type=Path,required=True)
    parser.add_argument('--metadata-db',type=Path,required=True)
    parser.add_argument('--state-dir',type=Path,required=True)
    parser.add_argument('--once',action='store_true')
    args = parser.parse_args()
    if os.getenv('TA_OBSERVER_SHADOW')!='1': raise SystemExit('EXPLICIT_SHADOW_OPT_IN_REQUIRED')
    args.state_dir.mkdir(parents=True,exist_ok=True)
    lock = open(args.state_dir/'writer.lock','a')
    try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError: raise SystemExit('SHADOW_WRITER_ALREADY_RUNNING')
    c = store.connect(args.state_dir/'shadow.db',int(time.time()*1000))
    source = readonly(args.source_db); metadata = readonly(args.metadata_db)
    stop = threading.Event()
    for sig in (signal.SIGINT,signal.SIGTERM): signal.signal(sig,lambda *_:stop.set())
    try:
        while not stop.is_set():
            now = int(time.time()*1000); health = dict(version=VERSION,ops_hash=OPS_HASH,observed_ms=now,
                states=Counter(),issues=Counter(),requested=0,new_setups=0)
            bounds = store.limits(args.state_dir)
            if not bounds['ok']:
                print('TA_SHADOW_PAUSED '+canonical(bounds),flush=True); return 3
            try:
                from long_short_swing_runtime import universe
                symbols = [row[0] for row in universe(now)]
                health['requested']=len(symbols)
                try: catalogue=tick_catalogue(metadata,now)
                except (ValueError,sqlite3.Error,KeyError):
                    catalogue=None; health['issues']['PRICE_FILTER_CATALOGUE_UNVERIFIED']+=1
                for index,symbol in enumerate(symbols):
                    if stop.is_set(): break
                    if index%10==0:
                        bounds=store.limits(args.state_dir)
                        if not bounds['ok']:
                            print('TA_SHADOW_PAUSED '+canonical(bounds),flush=True); return 3
                    try:
                        observed_ms=int(time.time()*1000)
                        result=scan_symbol(c,source,catalogue,symbol,observed_ms,args.state_dir)
                        health['states'][result['state']]+=1; health['new_setups']+=result['setups']
                        health['issues'].update(result.get('prefix_errors',[]))
                    except (ValueError,KeyError,TypeError,sqlite3.Error,OSError) as exc:
                        c.rollback()
                        kind=type(exc).__name__+':'+str(exc)[:140]
                        store.issue(c,now,symbol,kind); health['issues'][kind]+=1
                from .shadow_outcomes import measure_pending
                health['measurement']=measure_pending(c,source,int(time.time()*1000),args.state_dir)
            except (ValueError,sqlite3.Error,OSError) as exc:
                health['issues'][type(exc).__name__+':'+str(exc)[:140]]+=1
            health.update(store.counters(c)); health['resources']=store.limits(args.state_dir)
            health['finished_ms']=int(time.time()*1000)
            key=digest([VERSION,now]); c.execute('INSERT INTO health VALUES(?,?,?)',(key,now,canonical(health)));c.commit()
            print('TA_SHADOW_HEALTH '+canonical(health),flush=True)
            if args.once: break
            # Retry missing sources, and expose progress, without repeating closed-bar analysis.
            stop.wait(OPS['retry_seconds'])
    finally:
        source.close();metadata.close();c.close();lock.close()
    return 0


if __name__=='__main__': raise SystemExit(main())
