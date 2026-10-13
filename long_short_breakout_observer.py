"""Full eligible Binance Futures universe breakout OBSERVATION, versioned V1.

Independent of rotating deep selection, radar admission and core trade gates.
Never emits a trade authorization or places orders. All features are causal,
all volume bars are closed, setup references remain locked for their episode.
"""
import hashlib
import json
import math
import os
import signal
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit

from long_short_futures_kline_shadow import FuturesKlineStream, PERIOD_MS, connect as candle_connect, initialize as initialize_candles

CONFIG_PATH = Path(__file__).with_name('EARLY_BREAKOUT_V1_CONFIG.json')
CONFIG = json.loads(CONFIG_PATH.read_text())
CONFIG_HASH = hashlib.sha256(json.dumps(CONFIG,sort_keys=True,separators=(',',':')).encode()).hexdigest()
ALLOWED_SOURCES = {'BINANCE_UM_FUTURES_WEBSOCKET','BINANCE_UM_FUTURES_REST','BINANCE_UM_FUTURES_ARCHIVE'}
TERMINAL = {'BROKEN','EXPIRED','CHASE'}


def stamp(ms):
    return datetime.fromtimestamp(ms/1000,timezone.utc).isoformat()


def ema(values, period=7):
    result = values[0]
    alpha = 2/(period+1)
    for value in values[1:]:
        result += alpha*(value-result)
    return result


def atr(bars):
    return sum(max(b['high']-b['low'],abs(b['high']-a['close']),abs(b['low']-a['close']))
               for a,b in zip(bars,bars[1:]))/(len(bars)-1)


def read_snapshot(conn, symbol, now_ms, cfg=CONFIG):
    result = {}
    count = cfg['history_bars']
    for interval in ('1m','5m'):
        rows = conn.execute('''SELECT open_ms,close_ms,open,high,low,close,volume,
            quote_volume,taker_buy_quote,source FROM futures_closed_klines
            WHERE symbol=? AND interval=? AND close_ms<? ORDER BY open_ms DESC LIMIT ?''',
            (symbol,interval,now_ms-250,count)).fetchall()
        rows.reverse()
        if len(rows) != count:
            raise ValueError('HISTORY_INCOMPLETE:'+interval)
        if not 0 <= now_ms-rows[-1][1] <= cfg['max_'+interval+'_age_seconds']*1000:
            raise ValueError('CANDLES_STALE:'+interval)
        keys = ('open_ms','close_ms','open','high','low','close','volume','quote','buy_quote','source')
        bars = [dict(zip(keys,row)) for row in rows]
        for bar in bars:
            numbers = [bar[k] for k in keys[:-1]]
            if bar['source'] not in ALLOWED_SOURCES or any(v is None or not math.isfinite(float(v)) for v in numbers):
                raise ValueError('UNVERIFIED_CANDLE')
            if (bar['open_ms'] % PERIOD_MS[interval] or
                    bar['close_ms'] != bar['open_ms']+PERIOD_MS[interval]-1 or
                    min(bar['open'],bar['low'],bar['close']) <= 0 or
                    bar['high'] < max(bar['open'],bar['close']) or
                    bar['low'] > min(bar['open'],bar['close']) or
                    bar['volume'] < 0 or not 0 <= bar['buy_quote'] <= bar['quote']):
                raise ValueError('INVALID_CANDLE')
        if any(b['open_ms']-a['open_ms'] != PERIOD_MS[interval] for a,b in zip(bars,bars[1:])):
            raise ValueError('CANDLES_GAPPED:'+interval)
        result[interval] = bars
    price = conn.execute('SELECT price,event_ms,source FROM futures_last_price WHERE symbol=?',(symbol,)).fetchone()
    if (not price or price[2] != 'BINANCE_UM_FUTURES_WEBSOCKET' or
            not 0 <= now_ms-price[1] <= cfg['max_price_age_seconds']*1000 or
            not math.isfinite(price[0]) or price[0] <= 0):
        raise ValueError('LIVE_PRICE_UNAVAILABLE')
    result['price'] = float(price[0])
    result['price_event_ms'] = int(price[1])
    return result


def features(snapshot, cfg=CONFIG):
    one, five, price = snapshot['1m'],snapshot['5m'],snapshot['price']
    # The evaluated 5m candle is excluded from the reference box.
    box = five[-cfg['box_bars_5m']-1:-1]
    high,low = max(b['high'] for b in box),min(b['low'] for b in box)
    baseline = one[-cfg['volume_baseline_bars_1m']-1:-1]
    mean = sum(b['quote'] for b in baseline)/len(baseline)
    last = one[-1]
    if mean <= 0 or last['quote'] <= 0:
        raise ValueError('VOLUME_BASELINE_UNAVAILABLE')
    closes = [b['close'] for b in one]
    slope = (ema(closes)-ema(closes[:-1]))/price*100
    older = five[:-cfg['box_bars_5m']-1]
    return {'price':price,'local_high':high,'local_low':low,
        'price_event_ms':snapshot['price_event_ms'],
        'compression_pct':(high-low)/price*100,
        'volume_mult':last['quote']/mean,'taker_buy_share':last['buy_quote']/last['quote'],
        'ema7_slope_pct':slope,'atr_1m':atr(one[-16:]),
        'candle_time_ms':last['close_ms'],
        'reference_time_ms':box[-1]['close_ms'],
        'older_highs':[b['high'] for b in older], 'older_lows':[b['low'] for b in older]}


def evaluate(direction, snapshot, existing=None, now_ms=None, cfg=CONFIG):
    if direction not in ('LONG','SHORT'):
        raise ValueError('invalid direction')
    now_ms = int(time.time()*1000) if now_ms is None else now_ms
    f = features(snapshot,cfg)
    sign = 1 if direction == 'LONG' else -1
    price = f['price']
    if existing:
        level, invalidation = existing['level'],existing['invalidation']
    else:
        level = f['local_high'] if sign == 1 else f['local_low']
        invalidation = f['local_low'] if sign == 1 else f['local_high']
    distance = sign*(price/level-1)*100
    extension = max(0,distance)
    beyond = (f['older_highs'] if sign == 1 else f['older_lows'])
    obstacles = [p for p in beyond if sign*(p-price)>0]
    obstacle = min(obstacles) if sign == 1 and obstacles else max(obstacles) if obstacles else None
    room = sign*(obstacle/price-1)*100 if obstacle is not None else None
    # For SHORT, percent-to-lower-support is based on entry price, symmetric
    # with LONG percent-to-higher-resistance (no inverse-return inflation).
    ext_atr = extension/100*price/f['atr_1m'] if f['atr_1m']>0 else None
    flow = (f['taker_buy_share']>=cfg['min_taker_share'] if sign==1
            else f['taker_buy_share']<=1-cfg['min_taker_share'])
    reasons = []
    if ext_atr is None: reasons.append('VOLATILITY_BASELINE_UNAVAILABLE')
    if f['compression_pct']>cfg['max_compression_pct']: reasons.append('NO_COMPRESSION')
    if f['volume_mult']<cfg['min_volume_mult']: reasons.append('VOLUME_WEAK')
    if sign*f['ema7_slope_pct']<=0: reasons.append('EMA_NOT_ALIGNED')
    if not flow: reasons.append('FLOW_NOT_ALIGNED')
    f.update(level=level,invalidation=invalidation,distance_pct=distance,
             extension_pct=extension,extension_atr=ext_atr,known_room_pct=room,
             reasons=reasons,direction=direction,observation_only=True,
             config_version=cfg['version'],config_hash=CONFIG_HASH)
    if existing:
        if now_ms-existing['started_ms']>=cfg['setup_timeout_seconds']*1000:
            return 'EXPIRED',f
        # Two closed 1m bars returning inside the locked level invalidate an
        # observed breakout; a single live tick cannot invent a failed close.
        recent=snapshot['1m'][-2:]
        broke_stop=sign*(price-invalidation)<0
        returned_inside=all(sign*(bar['close']/level-1)*100 < -cfg['breakout_tolerance_pct']
                            and bar['close_ms']>(existing.get('early_ms') or now_ms) for bar in recent)
        if broke_stop or (existing['state'] in ('EARLY','CONFIRMED') and returned_inside):
            return 'BROKEN',f
        bar=snapshot['5m'][-1]
        span=max(bar['high']-bar['low'],1e-12)
        body=abs(bar['close']-bar['open'])/span
        wick=(bar['high']-max(bar['close'],bar['open']))/span if sign==1 else (min(bar['close'],bar['open'])-bar['low'])/span
        base=snapshot['5m'][-21:-1]
        base_mean=sum(b['quote'] for b in base)/len(base)
        cmult=bar['quote']/base_mean if base_mean>0 else 0
        confirmed=(existing.get('early_ms') is not None and bar['close_ms']>existing['early_ms']
                   and sign*(bar['close']-level)>0 and sign*(bar['close']-bar['open'])>0
                   and body>=cfg['confirmation_min_body_ratio']
                   and wick<=cfg['confirmation_max_rejection_wick']
                   and cmult>=cfg['confirmation_min_volume_mult'])
        f.update(confirmation_volume_mult=cmult,confirmation_body_ratio=body,
                 confirmation_candle_ms=bar['close_ms'])
        if existing['state']=='CONFIRMED' or confirmed:
            return 'CONFIRMED',f
    approach = distance >= -cfg['approach_pct']
    started = distance >= -cfg['breakout_tolerance_pct']
    chase = (extension>cfg['max_extension_pct'] or (ext_atr is not None and ext_atr>cfg['max_extension_atr'])
             or (room is not None and room<cfg['min_known_room_pct']))
    if approach and started and chase:
        return 'CHASE',f
    if not reasons and approach and started:
        return 'EARLY',f
    if existing and existing['state']=='EARLY':
        return 'EARLY',f  # weak follow-up volume alone does not create a new signal
    if approach and f['compression_pct']<=cfg['max_compression_pct']:
        return 'PREPARING',f
    return 'NONE',f


def initialize_state(path):
    os.makedirs(os.path.dirname(os.path.abspath(path)),exist_ok=True)
    c=sqlite3.connect(path,timeout=15)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('''CREATE TABLE IF NOT EXISTS episodes(
        id INTEGER PRIMARY KEY,symbol TEXT,direction TEXT,state TEXT,level REAL,invalidation REAL,
        started_ms INTEGER,early_ms INTEGER,early_price REAL,first_anomaly_ms INTEGER,
        first_anomaly_price REAL,confirmed_ms INTEGER,confirmed_price REAL,ended_ms INTEGER,
        gain_before_signal REAL,gain_before_confirmation REAL,mfe_pct REAL DEFAULT 0,
        mae_pct REAL DEFAULT 0,last_features_json TEXT,config_hash TEXT,config_version TEXT)''')
    c.execute('CREATE UNIQUE INDEX IF NOT EXISTS active_early_episode ON episodes(symbol,direction) WHERE ended_ms IS NULL')
    c.execute('''CREATE TABLE IF NOT EXISTS observations(
        symbol TEXT,direction TEXT,candle_ms INTEGER,observed_ms INTEGER,state TEXT,
        features_json TEXT,config_hash TEXT,PRIMARY KEY(symbol,direction,candle_ms,config_hash))''')
    c.execute('''CREATE TABLE IF NOT EXISTS transitions(
        id INTEGER PRIMARY KEY,episode_id INTEGER,observed_ms INTEGER,old_state TEXT,new_state TEXT,
        features_json TEXT,delivery_status TEXT,config_hash TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS data_issues(
        id INTEGER PRIMARY KEY,observed_ms INTEGER,symbol TEXT,reason TEXT,config_hash TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS outcomes(
        episode_id INTEGER,horizon_seconds INTEGER,state TEXT,last_ms INTEGER,
        mfe_pct REAL,mae_pct REAL,return_pct REAL,reach_json TEXT,config_hash TEXT,
        PRIMARY KEY(episode_id,horizon_seconds))''')
    c.commit()
    return c


def observe(conn,symbol,snapshot,now_ms,cfg=CONFIG):
    updates=[]
    for direction in ('LONG','SHORT'):
        row=conn.execute('SELECT * FROM episodes WHERE symbol=? AND direction=? AND ended_ms IS NULL',
                         (symbol,direction)).fetchone()
        existing=dict(row) if row else None
        state,f=evaluate(direction,snapshot,existing,now_ms,cfg)
        encoded=json.dumps(f,ensure_ascii=False,allow_nan=False)
        conn.execute('INSERT OR IGNORE INTO observations VALUES(?,?,?,?,?,?,?)',
                     (symbol,direction,f['candle_time_ms'],now_ms,state,encoded,CONFIG_HASH))
        if existing is None and state not in ('NONE','CHASE'):
            recent=conn.execute('SELECT MAX(ended_ms) FROM episodes WHERE symbol=? AND direction=?',
                                (symbol,direction)).fetchone()[0]
            if recent is not None and now_ms-recent<cfg['cooldown_seconds']*1000:
                continue
            anomalous=(f['volume_mult']>=cfg['min_volume_mult'] and
                       (1 if direction=='LONG' else -1)*f['ema7_slope_pct']>0)
            cur=conn.execute('''INSERT INTO episodes(symbol,direction,state,level,invalidation,
                started_ms,first_anomaly_ms,first_anomaly_price,last_features_json,config_hash,config_version)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                (symbol,direction,'PREPARING',f['level'],f['invalidation'],now_ms,now_ms if anomalous else None,
                 snapshot['price'] if anomalous else None,encoded,CONFIG_HASH,cfg['version']))
            existing=dict(conn.execute('SELECT * FROM episodes WHERE id=?',(cur.lastrowid,)).fetchone())
        if existing is None:
            continue
        old=existing['state']
        early_ms,early_price=existing['early_ms'],existing['early_price']
        before_signal=existing['gain_before_signal']
        sign=1 if direction=='LONG' else -1
        if existing['first_anomaly_ms'] is None and f['volume_mult']>=cfg['min_volume_mult'] and sign*f['ema7_slope_pct']>0:
            existing['first_anomaly_ms']=now_ms
            existing['first_anomaly_price']=snapshot['price']
            conn.execute('UPDATE episodes SET first_anomaly_ms=?,first_anomaly_price=? WHERE id=?',
                         (now_ms,snapshot['price'],existing['id']))
        if state=='EARLY' and early_ms is None:
            early_ms,early_price=now_ms,snapshot['price']
            before_signal=(sign*(early_price/existing['first_anomaly_price']-1)*100
                           if existing['first_anomaly_price'] else None)
        confirmed_ms,confirmed_price=existing['confirmed_ms'],existing['confirmed_price']
        before_confirmation=existing['gain_before_confirmation']
        if state=='CONFIRMED' and confirmed_ms is None:
            confirmed_ms,confirmed_price=now_ms,snapshot['price']
            before_confirmation=sign*(confirmed_price/early_price-1)*100
        terminal=state in TERMINAL
        conn.execute('''UPDATE episodes SET state=?,early_ms=?,early_price=?,confirmed_ms=?,
            confirmed_price=?,ended_ms=?,gain_before_signal=?,gain_before_confirmation=?,last_features_json=? WHERE id=?''',
            (state,early_ms,early_price,confirmed_ms,confirmed_price,now_ms if terminal else None,
             before_signal,before_confirmation,encoded,existing['id']))
        if state!=old:
            cur=conn.execute('INSERT INTO transitions(episode_id,observed_ms,old_state,new_state,features_json,delivery_status,config_hash) VALUES(?,?,?,?,?,?,?)',
                (existing['id'],now_ms,old,state,encoded,'PENDING' if early_ms is not None else 'SILENT',CONFIG_HASH))
            if early_ms is not None:
                updates.append({'transition_id':cur.lastrowid,'symbol':symbol,'direction':direction,
                    'state':state,'features':f,'early_ms':early_ms,'early_price':early_price,
                    'gain_before_confirmation':before_confirmation})
    conn.commit()
    return updates


def format_update(item,now_ms):
    d=item['direction']; f=item['features']; level=f['level']
    side='üstünde' if d=='LONG' else 'altında'
    states={'EARLY':'ERKEN KIRILIM','CONFIRMED':'5 DK KAPANIŞ TEYİDİ',
            'BROKEN':'KIRILIM BOZULDU','CHASE':'GEÇ / KOVALAMA','EXPIRED':'İZLEME SÜRESİ DOLDU'}
    title=states.get(item['state'],'TEYİT BEKLİYOR')
    icon='🟢' if d=='LONG' else '🔴'
    from long_short_display import analysis_fields,analysis_line,local_time
    analyst_db=os.getenv('LS_ANALYST_DB') or os.getenv('LS_DB') or os.path.join(
        os.getenv('LS_STATE_DIR') or os.getenv('RAILWAY_VOLUME_MOUNT_PATH') or '.long-short-state',
        'long_short_analyst.db')
    values=analysis_line(analysis_fields(item['symbol'],analyst_db))
    lines=[f'{icon} {d} | {item["symbol"]} — {title}',
           f'Son ölçülen fiyat: {f["price"]:.8g} | Kırılım: {level:.8g}',
           f'İlk erken uyarı: {item["early_price"]:.8g} | {local_time(stamp(item["early_ms"]))}',
           f'📊 Kısa yön: {"yukarı" if f["ema7_slope_pct"]>0 else "aşağı"} | Hacim 1 dk: {f["volume_mult"]:.2f}× | Alış payı: %{f["taker_buy_share"]*100:.0f} | {values}']
    if item['state']=='EARLY':
        lines.append(f'Beklenen: 5 dk mum {level:.8g} {side} kapanmalı, ardından seviyeyi korumalı.')
    elif item['state']=='CONFIRMED':
        lines.append('Kapanış teyidi oluştu; ana motorun giriş onayı ayrıca bekleniyor.')
    elif item['state']=='BROKEN':
        lines.append('İlk kırılım korunamadı; bu erken fikir artık geçerli değil.')
    elif item['state']=='CHASE':
        lines.append('Hareket uzadı veya sonraki seviyeye alan daraldı; erken fırsat sınıfından çıktı.')
    else:
        lines.append('Bu gözlem kapandı; yeni hazırlık bekleniyor.')
    lines.append('Erken kırılım gözlemi; işlem giriş teyidi değildir.')
    lines.append('Güncelleme: '+datetime.fromtimestamp(now_ms/1000,timezone.utc).astimezone(__import__('zoneinfo').ZoneInfo('Europe/Istanbul')).strftime('%H:%M:%S'))
    return '\n'.join(lines)


def label_outcomes(state,candles,now_ms,cfg=CONFIG):
    """Post-observation 1m paths only; raw outcomes, never simulated trade PnL.

    Discard the partially elapsed signal minute because its high/low may
    precede the alert. Missing paths remain unresolved and are retried.
    """
    rows=state.execute('''SELECT e.* FROM episodes e WHERE early_ms IS NOT NULL
        AND (SELECT COUNT(*) FROM outcomes o WHERE o.episode_id=e.id AND o.state='RESOLVED')<?''',
        (len(cfg['outcome_horizons_seconds']),)).fetchall()
    for row in rows:
        begin=((row['early_ms']+59999)//60000)*60000
        sign=1 if row['direction']=='LONG' else -1
        for horizon in cfg['outcome_horizons_seconds']:
            old=state.execute('SELECT state FROM outcomes WHERE episode_id=? AND horizon_seconds=?',
                              (row['id'],horizon)).fetchone()
            if old and old[0]=='RESOLVED': continue
            end=begin+horizon*1000
            if end>now_ms-250: continue
            bars=candles.execute('''SELECT open_ms,close_ms,high,low,close,source
                FROM futures_closed_klines WHERE symbol=? AND interval='1m'
                AND open_ms>=? AND open_ms<? ORDER BY open_ms''',(row['symbol'],begin,end)).fetchall()
            expected=list(range(begin,end,60000))
            if ([bar[0] for bar in bars]!=expected or
                any(bar[1]!=bar[0]+59999 or bar[5] not in ALLOWED_SOURCES or
                    not all(v is not None and math.isfinite(v) and v>0 for v in bar[2:5])
                    or bar[2]<bar[3] for bar in bars)):
                state.execute('INSERT OR REPLACE INTO outcomes VALUES(?,?,?,?,?,?,?,?,?)',
                    (row['id'],horizon,'DATA_PENDING',now_ms,None,None,None,'{}',CONFIG_HASH))
                continue
            entry=row['early_price']
            favorable=[sign*((bar[2] if sign==1 else bar[3])/entry-1)*100 for bar in bars]
            adverse=[sign*((bar[3] if sign==1 else bar[2])/entry-1)*100 for bar in bars]
            reach={}
            for target in cfg['reach_targets_pct']:
                hit=next((bar for bar,gain in zip(bars,favorable) if gain>=target),None)
                reach[str(target)]={'reached':hit is not None,
                    'time_to_reach_upper_bound_seconds':(hit[1]+1-row['early_ms'])/1000 if hit else None}
            state.execute('INSERT OR REPLACE INTO outcomes VALUES(?,?,?,?,?,?,?,?,?)',
                (row['id'],horizon,'RESOLVED',now_ms,max(0,max(favorable)),min(0,min(adverse)),
                 sign*(bars[-1][4]/entry-1)*100,json.dumps(reach),CONFIG_HASH))
            state.execute('UPDATE episodes SET mfe_pct=MAX(mfe_pct,?),mae_pct=MIN(mae_pct,?) WHERE id=?',
                          (max(0,max(favorable)),min(0,min(adverse)),row['id']))
    state.commit()


def deliver_pending(state,token,chat,now_ms,publisher=None):
    if os.getenv('LS_TELEGRAM_TRADE_ONLY','0').lower() in ('1','true','yes','on'):
        state.execute("""UPDATE transitions SET delivery_status='SILENT_POLICY'
            WHERE delivery_status='PENDING' OR delivery_status LIKE 'FAILED:%'""")
        state.commit()
        return  # observation/outcome tracking continues; no user notifications
    from long_short_message_card import publish
    publisher=publisher or publish
    # At most three distinct coins per poll; state updates edit existing cards.
    # Latest transition supersedes obsolete queued ideas, not a 20-minute delay.
    rows=state.execute('''SELECT t.id AS transition_id,t.observed_ms,t.new_state,
        e.* FROM transitions t JOIN episodes e ON e.id=t.episode_id
        WHERE (t.delivery_status='PENDING' OR t.delivery_status LIKE 'FAILED:%')
        AND t.id=(SELECT MAX(t2.id) FROM transitions t2 WHERE t2.episode_id=t.episode_id)
        ORDER BY t.observed_ms LIMIT 3''').fetchall()
    state.execute('''UPDATE transitions SET delivery_status='SUPERSEDED'
        WHERE (delivery_status='PENDING' OR delivery_status LIKE 'FAILED:%')
        AND id NOT IN (SELECT MAX(id) FROM transitions GROUP BY episode_id)''')
    for row in rows:
        if row['new_state']=='EARLY' and now_ms-row['observed_ms']>90000:
            status='STALE_SUPPRESSED'
        else:
            item={'symbol':row['symbol'],'direction':row['direction'],'state':row['state'],
                'features':json.loads(row['last_features_json']),'early_ms':row['early_ms'],
                'early_price':row['early_price'],'gain_before_confirmation':row['gain_before_confirmation']}
            try:
                result=publisher(token,chat,row['symbol'],'EARLY',format_update(item,now_ms),
                                 row['direction'],observed_at=stamp(now_ms))
                status=result['status']
            except Exception as exc:
                status='FAILED:'+type(exc).__name__
        state.execute('UPDATE transitions SET delivery_status=? WHERE id=?',(status,row['transition_id']))
    state.commit()


def eligible_symbols():
    from long_short_analyst import native_futures_websocket_universe
    return [row[0] for row in native_futures_websocket_universe(quiet=True,all_eligible=True)]


def partition_symbols(symbols,size=CONFIG['feed_symbols_per_connection']):
    unique=sorted(set(symbols))
    if len(unique)>2*size:
        raise RuntimeError('EARLY_FEED_CAPACITY_EXCEEDED')
    return [unique[:size],unique[size:]]


def run():
    from long_short_futures_history_worker import repair
    from long_short_live_pool import resolve_live_chat_id
    root=os.getenv('LS_STATE_DIR') or os.getenv('RAILWAY_VOLUME_MOUNT_PATH') or '.long-short-state'
    db=os.path.join(root,'binance_futures_kline_shadow.db')
    initialize_candles(db).close()
    state=initialize_state(os.path.join(root,'long_short_breakout_observer.db'))
    stop=threading.Event(); lock=threading.Lock(); universe=[]
    for sig in (signal.SIGINT,signal.SIGTERM): signal.signal(sig,lambda *_:stop.set())
    base=os.getenv('LS_FUTURES_HISTORY_RELAY_URL','').rstrip('/')
    url=urlsplit(base)
    if (url.scheme!='http' or not (url.hostname or '').endswith('.railway.internal')
        or url.username or url.password or url.path or url.query or url.fragment):
        raise ValueError('observer bootstrap requires verified private Railway relay')
    token=os.getenv('TELEGRAM_BOT_TOKEN','').strip()
    chat=resolve_live_chat_id(token,os.getenv('TELEGRAM_CHAT_ID','').strip()) if token else ''
    shard_size=CONFIG['feed_symbols_per_connection']
    feeds=[]; feed_threads=[]
    def feed_loop(stream,requested):
        while not stop.is_set():
            try:
                stream.run(requested,stop,refresh_seconds=15)
            except Exception as exc:
                print('EARLY_FEED_WORKER_ERROR',type(exc).__name__,flush=True)
            stop.wait(5)
    # Verified ASCII USDT catalogue contains fewer than 800 symbols today;
    # excess is explicitly unhealthy, never silently truncated as full coverage.
    for index in range(2):
        def requested(i=index):
            with lock: return sorted(universe)[i*shard_size:(i+1)*shard_size]
        stream=FuturesKlineStream(db,intervals=('1m','5m'),max_symbols=shard_size,retention_seconds=0)
        t=threading.Thread(target=feed_loop,args=(stream,requested),daemon=True)
        t.start(); feeds.append(stream);feed_threads.append(t)

    def bootstrap():
        next_due={}
        while not stop.is_set():
            with lock: symbols=list(universe)
            for symbol in symbols:
                if stop.is_set(): return
                if time.monotonic()<next_due.get(symbol,0): continue
                try:
                    c=candle_connect(db)
                    try:
                        now=int(time.time()*1000)
                        for iv in ('1m','5m'):
                            rows=c.execute('SELECT open_ms,close_ms FROM futures_closed_klines WHERE symbol=? AND interval=? ORDER BY open_ms DESC LIMIT 30',(symbol,iv)).fetchall()
                            ready=(len(rows)==30 and 0<=now-rows[0][1]<=CONFIG['max_'+iv+'_age_seconds']*1000
                                   and all(a[0]-b[0]==PERIOD_MS[iv] for a,b in zip(rows,rows[1:])))
                            if not ready:
                                repair(db,base,symbol,iv)
                                if stop.wait(CONFIG['bootstrap_request_gap_seconds']): return
                    finally: c.close()
                    next_due[symbol]=time.monotonic()+60
                except Exception as exc:
                    print('EARLY_BOOTSTRAP_ERROR',symbol,type(exc).__name__,flush=True)
                    if isinstance(exc,HTTPError) and exc.code==400:
                        next_due[symbol]=time.monotonic()+3600
                    else:
                        stop.wait(60); break  # relay-wide rate/outage backoff
            stop.wait(15)
    threading.Thread(target=bootstrap,daemon=True).start()
    refresh_at=0; health_at=0
    try:
        while not stop.is_set():
            now_ms=int(time.time()*1000)
            universe_valid=True
            if time.monotonic()>=refresh_at:
                try:
                    current=eligible_symbols()
                    partition_symbols(current,shard_size)
                    with lock: universe[:]=current
                except Exception as exc:
                    universe_valid=False
                    state.execute('INSERT INTO data_issues(observed_ms,symbol,reason,config_hash) VALUES(?,?,?,?)',(now_ms,'UNIVERSE',str(exc)[:160],CONFIG_HASH));state.commit()
                    with lock: universe.clear()  # no unverified catalogue reuse
                refresh_at=time.monotonic()+CONFIG['universe_refresh_seconds']
            with lock: symbols=list(universe)
            ready=0; issues={}
            c=candle_connect(db)
            try:
                for symbol in symbols if universe_valid else []:
                    try:
                        snapshot=read_snapshot(c,symbol,now_ms)
                        observe(state,symbol,snapshot,now_ms)
                        ready+=1
                    except (ValueError,sqlite3.Error) as exc:
                        reason=str(exc).split(':')[0]
                        issues[reason]=issues.get(reason,0)+1
                        if time.monotonic()>=health_at:
                            state.execute('INSERT INTO data_issues(observed_ms,symbol,reason,config_hash) VALUES(?,?,?,?)',(now_ms,symbol,str(exc)[:160],CONFIG_HASH))
                state.commit()
                if time.monotonic()>=health_at:
                    label_outcomes(state,c,now_ms)
            finally: c.close()
            deliver_pending(state,token,chat,now_ms)
            if time.monotonic()>=health_at:
                stages={r[0]:r[1] for r in state.execute('SELECT state,COUNT(*) FROM episodes WHERE ended_ms IS NULL GROUP BY state')}
                delivery={r[0]:r[1] for r in state.execute('SELECT delivery_status,COUNT(*) FROM transitions GROUP BY delivery_status')}
                print('EARLY_UNIVERSE_HEALTH',json.dumps({'eligible':len(symbols),'ready':ready,
                    'invalid':len(symbols)-ready,'issues':issues,'version':CONFIG['version'],
                    'config_hash':CONFIG_HASH,'observation_only':True,
                    'active_stages':stages,'delivery_status':delivery,
                    'feeds':[s.coverage(symbols[i*shard_size:(i+1)*shard_size]) for i,s in enumerate(feeds)]}),flush=True)
                health_at=time.monotonic()+60
            stop.wait(CONFIG['poll_seconds'])
    finally:
        stop.set()
        for t in feed_threads:t.join(timeout=2)
        state.close()


if __name__=='__main__':
    run()
