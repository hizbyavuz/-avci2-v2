"""Bounded shared-data reads and incremental post-notification evidence."""
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

from long_short_major_move import CONFIG, CONFIG_HASH, VERSION, finite

SOURCES={'BINANCE_UM_FUTURES_WEBSOCKET','BINANCE_UM_FUTURES_ARCHIVE','BINANCE_UM_FUTURES_REST'}
EXCLUDED={'USDC','FDUSD','TUSD','USDP','DAI','BUSD','USDE','USDS','PYUSD','RLUSD','USD1','USDD','GUSD','FRAX','LUSD','USD0','EURC','EUR','TRY','BTCST'}


def root():
    return Path(os.getenv('LS_STATE_DIR') or os.getenv('RAILWAY_VOLUME_MOUNT_PATH') or '.long-short-state')


def db(path,readonly=False):
    c=sqlite3.connect(Path(path).resolve().as_uri()+('?mode=ro' if readonly else '?mode=rwc'),uri=True,timeout=3)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA cache_size=-2048');c.execute('PRAGMA temp_store=FILE')
    return c


def connect():
    root().mkdir(parents=True,exist_ok=True)
    c=db(root()/'major_move_v1.db');c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''CREATE TABLE IF NOT EXISTS manifests(id TEXT PRIMARY KEY,payload TEXT);
      CREATE TABLE IF NOT EXISTS assessments(id TEXT PRIMARY KEY,observed_ms INTEGER,symbol TEXT,stage TEXT,payload TEXT);
      CREATE INDEX IF NOT EXISTS major_assessments_time ON assessments(observed_ms);
      CREATE TABLE IF NOT EXISTS ideas(id TEXT PRIMARY KEY,symbol TEXT,created_ms INTEGER,delivered_ms INTEGER,
        state TEXT,payload TEXT,progress TEXT,updated_ms INTEGER);
      CREATE INDEX IF NOT EXISTS major_ideas_time ON ideas(delivered_ms,state);
      CREATE TABLE IF NOT EXISTS outcomes(idea_id TEXT,horizon_hours INTEGER,state TEXT,payload TEXT,
        PRIMARY KEY(idea_id,horizon_hours));
      CREATE TABLE IF NOT EXISTS market_snapshots(bucket INTEGER,symbol TEXT,payload TEXT,PRIMARY KEY(bucket,symbol));''')
    return c


def universe(now_ms=None):
    now_ms=int(time.time()*1000) if now_ms is None else now_ms
    c=db(root()/'native_derivatives_v2.db',True)
    try:catalog=c.execute('SELECT observed_ms,payload FROM markets WHERE id=1').fetchone()
    finally:c.close()
    if not catalog or not 0<=now_ms-catalog[0]<=3600000:raise ValueError('CATALOGUE_STALE')
    allowed=set(json.loads(catalog[1]));c=db(root()/'binance_futures_universe.db',True)
    try:rows=c.execute('SELECT symbol,quote_volume,last_price,change_pct,received_ms,source FROM futures_tickers').fetchall()
    finally:c.close()
    result=[]
    for s,q,p,change,stamp,source in rows:
        base=s[:-4]
        if (s not in allowed or not s.endswith('USDT') or not base.isalnum() or base in EXCLUDED or
            any(base.endswith(x) for x in ['UP','DOWN','BULL','BEAR']) or
            source!='BINANCE_UM_FUTURES_WEBSOCKET' or not 0<=now_ms-stamp<=120000 or
            not all(finite(x) for x in [q,p,change]) or q<CONFIG['min_quote_volume'] or p<=0):continue
        result.append((s,q,p,change))
    if not result:raise ValueError('UNIVERSE_UNAVAILABLE')
    return sorted(result,key=lambda r:r[1],reverse=True)


def demand(limit=90,full_chart=False):
    now=int(time.time()*1000);urgent=[]
    path=root()/'major_move_v1.db'
    if path.exists():
        c=db(path,True)
        try:urgent=[r[0] for r in c.execute("SELECT DISTINCT symbol FROM ideas WHERE delivered_ms>=? AND state NOT IN ('DONE','UNRESOLVED_ENTRY') ORDER BY delivered_ms DESC",(now-73*3600000,))]
        finally:c.close()
    values=universe(now);symbols=[r[0] for r in values]
    # When the native feed cap is reached, keep current evidence first and
    # rotate remaining demand. Daily movers get warmup, never automatic signals.
    movers=[r[0] for r in sorted(values,key=lambda r:abs(r[3]),reverse=True)[:12]]
    offset=(now//900000*24)%len(symbols);rotation=symbols[offset:]+symbols[:offset]
    requested=list(dict.fromkeys(['BTCUSDT']+urgent+movers+rotation+['ETHUSDT','SOLUSDT']))
    return requested[:limit],urgent[:limit]


def quote(c,symbol,now_ms):
    r=c.execute('SELECT price,event_ms,received_ms,source FROM futures_last_price WHERE symbol=?',(symbol,)).fetchone()
    if not r or r[3]!='BINANCE_UM_FUTURES_WEBSOCKET' or not all(0<=now_ms-int(t)<=CONFIG['quote_max_age_ms'] for t in r[1:3]):return None
    return r[0] if finite(r[0]) and r[0]>0 else None


def frames(c,symbol):
    result={}
    for tf,n in [('15m',120),('1h',120),('4h',220)]:
        rows=c.execute('SELECT open_ms,open,high,low,close,volume,close_ms,source FROM futures_closed_klines WHERE symbol=? AND interval=? ORDER BY open_ms DESC LIMIT ?',(symbol,tf,n)).fetchall()
        if any(r[7] not in SOURCES for r in rows):raise ValueError('UNVERIFIED_SOURCE_'+tf)
        result[tf]=[list(r[:7]) for r in reversed(rows)]
    return result


def save_assessment(c,p,sid):
    key=hashlib.sha256(json.dumps([sid,p['symbol'],p['observed_ms']//900000,p['stage'],p['reason']],sort_keys=True).encode()).hexdigest()
    c.execute('INSERT OR IGNORE INTO assessments VALUES(?,?,?,?,?)',(key,p['observed_ms'],p['symbol'],p['stage'],json.dumps(p)))


def initial_progress(p):
    return dict(cursor_ms=p['observed_ms']//60000*60000,first_barrier=None,
        boundary_ambiguous=False,max_favorable_pct=0.,max_adverse_pct=0.,milestones={},plan_exit=None,
        active_stop=p['stop'],hour_bar=None,trail_armed=False,
        evaluated_ms=p['observed_ms'],last_close=p['price'],missing_since_ms=None)


def advance_path(p,progress,bars):
    """Streaming OHLC bounds; retain path after the stop for reversal analysis.

    A 1m gap pauses the cursor for repair. No jumping over an unknown segment.
    The partial first minute cannot determine barrier order; label it unknown.
    """
    g=json.loads(json.dumps(progress));price=p['price'];sign=1 if p['direction']=='LONG' else -1
    outputs=[]
    for r in bars:
        t,op,hi,lo,cl,end,source=r
        if t!=g['cursor_ms']:
            g['missing_since_ms']=g['cursor_ms'];break
        if (source not in SOURCES or end!=t+59999 or
            not all(finite(v) and v>0 for v in [op,hi,lo,cl]) or not lo<=min(op,cl)<=max(op,cl)<=hi):
            g['missing_since_ms']=t;break
        partial=t<p['observed_ms'];risk=g.get('active_stop',p['stop']);target=p['objective']
        stop_hit=lo<=risk if sign==1 else hi>=risk
        target_hit=hi>=target if sign==1 else lo<=target
        if partial and stop_hit:g['boundary_ambiguous']=True
        observed_hi=max(price,cl) if partial else hi
        observed_lo=min(price,cl) if partial else lo
        favorable=sign*((observed_hi if sign==1 else observed_lo)/price-1)*100
        adverse=-sign*((observed_lo if sign==1 else observed_hi)/price-1)*100
        g['max_favorable_pct']=max(g['max_favorable_pct'],favorable)
        g['max_adverse_pct']=max(g['max_adverse_pct'],adverse)
        for threshold in CONFIG['reach_thresholds_pct']:
            if favorable+1e-10>=threshold:g['milestones'].setdefault(str(threshold),[max(t,p['observed_ms']),end])
        if not partial and g['first_barrier'] is None and (stop_hit or target_hit):
            g['first_barrier']=dict(kind='STOP' if stop_hit else 'OBJECTIVE',minute_open_ms=t,
                same_minute_ambiguous=bool(stop_hit and target_hit))
        if not g['boundary_ambiguous'] and g['plan_exit'] is None:
            if not partial and stop_hit:
                exit_price=min(op,risk) if sign==1 else max(op,risk)
                g['plan_exit']=dict(kind='STOP',at_ms=end,stop_price=risk,
                    net_return_pct=sign*(exit_price/price-1)*100-CONFIG['round_trip_cost_pct'])
            elif end+1>=p['expires_ms']:
                g['plan_exit']=dict(kind='TIMEOUT',at_ms=end,net_return_pct=sign*(cl/price-1)*100-CONFIG['round_trip_cost_pct'])
        # A complete post-entry UTC hour sets NEXT minute's trailing stop.
        # The hour that first touches +10% cannot retroactively stop itself out.
        hb=g.get('hour_bar')
        if t%3600000==0 and not partial:hb=dict(start=t,low=lo,high=hi,count=0)
        if hb:
            hb.update(low=min(hb['low'],lo),high=max(hb['high'],hi),count=hb['count']+1)
        if not partial and target_hit:g['trail_armed']=True
        if (end+1)%3600000==0:
            if hb and hb['count']==60 and g['trail_armed'] and not g['boundary_ambiguous'] and not g['plan_exit']:
                buffer=CONFIG['stop_buffer_atr1h']*p['features']['atr1h']
                candidate=hb['low']-buffer if sign==1 else hb['high']+buffer
                g['active_stop']=max(risk,candidate) if sign==1 else min(risk,candidate)
            hb=None
        g['hour_bar']=hb
        g.update(cursor_ms=t+60000,evaluated_ms=end,last_close=cl,missing_since_ms=None)
        for hours in CONFIG['horizons_hours']:
            due=p['observed_ms']+hours*3600000
            if t<due<=end+1:
                out=dict(horizon_hours=hours,directional_return_pct=sign*(cl/price-1)*100,
                    max_favorable_pct=g['max_favorable_pct'],max_adverse_pct=g['max_adverse_pct'],
                    milestones=dict(g['milestones']),first_barrier=g['first_barrier'],plan_exit=g['plan_exit'],active_stop=g['active_stop'],
                    first_minute_ambiguous=g['boundary_ambiguous'],cost_assumption_pct=CONFIG['round_trip_cost_pct'])
                if not g['boundary_ambiguous']:
                    out['reference_net_return_pct']=(g['plan_exit']['net_return_pct'] if g['plan_exit'] else sign*(cl/price-1)*100-CONFIG['round_trip_cost_pct'])
                outputs.append(out)
        if end+1>=p['observed_ms']+72*3600000:break
    return g,outputs


def measure(c,kdb,now_ms,emit=print):
    rows=c.execute("SELECT * FROM ideas WHERE delivered_ms IS NOT NULL AND state NOT IN ('DONE','DONE_DATA_GAP','UNRESOLVED_ENTRY') ORDER BY updated_ms LIMIT ?",(CONFIG['outcome_batch'],)).fetchall()
    for row in rows:
        p=json.loads(row['payload']);g=json.loads(row['progress'])
        bars=kdb.execute("SELECT open_ms,open,high,low,close,close_ms,source FROM futures_closed_klines WHERE symbol=? AND interval='1m' AND open_ms>=? AND close_ms<? ORDER BY open_ms LIMIT ?",(p['symbol'],g['cursor_ms'],now_ms-250,CONFIG['candle_batch'])).fetchall()
        g,results=advance_path(p,g,bars)
        if not bars and g['cursor_ms']+60000<now_ms-250:g['missing_since_ms']=g['cursor_ms']
        state='TRACKING'
        if g['evaluated_ms']+1>=p['observed_ms']+72*3600000:state='DONE'
        elif g['missing_since_ms'] is not None:state='DATA_PENDING'
        if state=='DATA_PENDING' and now_ms>p['observed_ms']+96*3600000:state='DONE_DATA_GAP'
        c.execute('UPDATE ideas SET state=?,progress=?,updated_ms=? WHERE id=?',(state,json.dumps(g),now_ms,row['id']))
        for result in results:
            status='UNRESOLVED_BOUNDARY' if result['first_minute_ambiguous'] else 'RESOLVED'
            c.execute("""INSERT INTO outcomes VALUES(?,?,?,?) ON CONFLICT(idea_id,horizon_hours)
                DO UPDATE SET state=excluded.state,payload=excluded.payload
                WHERE outcomes.state='DATA_PENDING'""",(row['id'],result['horizon_hours'],status,json.dumps(result)))
            emit('MAJOR_MOVE_OUTCOME '+json.dumps(dict(idea_id=row['id'],symbol=p['symbol'],state=status,**result)))
        # Mature gaps remain explicit and can be repaired on the next pass.
        for hours in CONFIG['horizons_hours']:
            if now_ms>=p['observed_ms']+hours*3600000 and g['evaluated_ms']<p['observed_ms']+hours*3600000-1 and g['missing_since_ms'] is not None:
                c.execute('INSERT OR IGNORE INTO outcomes VALUES(?,?,?,?)',(row['id'],hours,'DATA_PENDING',json.dumps({'missing_from_ms':g['missing_since_ms']})))
        c.commit()


def health(state_root=None):
    path=(Path(state_root) if state_root else root())/'major_move_v1.db'
    if not path.exists():return dict(profile=VERSION,available=False)
    c=db(path,True)
    try:return dict(profile=VERSION,available=True,latest_scan_ms=c.execute('SELECT MAX(observed_ms) FROM assessments').fetchone()[0],
        ideas=dict(c.execute('SELECT state,COUNT(*) FROM ideas GROUP BY state')),
        outcomes=dict(c.execute('SELECT state,COUNT(*) FROM outcomes GROUP BY state')))
    finally:c.close()
