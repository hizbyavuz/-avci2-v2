"""Versioned observation sidecar. Nothing here is consulted by assess/admission.

Event time and receipt time are distinct. Retrospective REST enrichment is never
presented as a feature known when the original signal was delivered.
"""
import hashlib
import json
import math
from functools import lru_cache
from pathlib import Path

from .core import CFG, MINUTE, closed_bars, finite, ema

REVISION = 'OBSERVATIONS_20261027_1'
DAY = 1440 * MINUTE


def encoded(value):
    return json.dumps(value, sort_keys=True, allow_nan=False, separators=(',', ':'))


@lru_cache(maxsize=1)
def protocol_hash():
    path=Path(__file__).resolve().parent.parent/'research/ta_paper_final_20261027/protocol.json'
    return hashlib.sha256(path.read_bytes()).hexdigest()


def init(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS candle_aux(symbol TEXT,tf TEXT,ms INTEGER,payload TEXT,
        received_ms INTEGER,source TEXT,PRIMARY KEY(symbol,tf,ms));
      CREATE TABLE IF NOT EXISTS aux_issues(id INTEGER PRIMARY KEY,ms INTEGER,symbol TEXT,reason TEXT);
      CREATE TABLE IF NOT EXISTS feature_events(id INTEGER PRIMARY KEY,key TEXT,kind TEXT,
        event_ms INTEGER,received_ms INTEGER,payload TEXT,UNIQUE(key,kind));
      CREATE TABLE IF NOT EXISTS feature_jobs(key TEXT PRIMARY KEY,symbol TEXT,signal_ms INTEGER,
        next_ms INTEGER,attempts INTEGER,payload TEXT);
    ''')


def kline_aux(raw):
    """Binance field indexes; Bybit adapter supplies only quote volume at 7."""
    result = dict(quote_volume=None, trade_count=None, taker_buy_base=None,
                  taker_buy_quote=None, taker_buy_share=None, status='MISSING')
    if len(raw) < 8:
        return result
    q = finite(raw[7]); volume = finite(raw[5])
    if q < 0 or volume < 0:
        raise ValueError('NEGATIVE_AUX_VOLUME')
    result.update(quote_volume=q, status='QUOTE_ONLY')
    if len(raw) >= 11:
        trades = finite(raw[8]); base = finite(raw[9]); quote = finite(raw[10])
        if (trades < 0 or int(trades) != trades or base < 0 or quote < 0 or
                base > volume + max(1e-10, volume*1e-9) or quote > q + max(1e-8, q*1e-9)):
            raise ValueError('INVALID_TAKER_FIELDS')
        result.update(trade_count=int(trades), taker_buy_base=base, taker_buy_quote=quote,
                      taker_buy_share=quote/q if q else None, status='OK' if q else 'ZERO_VOLUME')
    return result


def capture_klines(db, symbol, tf, rows, now, source):
    period = {'1m': MINUTE, '30m': CFG.entry_ms, '4h': CFG.context_ms}[tf]
    for raw in rows:
        try:
            valid = closed_bars([raw], period, now, minimum=0, require_current=False)
            if not valid:
                continue
            fields = kline_aux(raw)
            if fields['status'] == 'MISSING':
                continue
            old = db.execute('SELECT payload FROM candle_aux WHERE symbol=? AND tf=? AND ms=?',
                             (symbol, tf, int(raw[0]))).fetchone()
            if old and old[0] != encoded(fields):
                raise ValueError('AUX_CLOSED_CANDLE_CHANGED')
            db.execute('INSERT OR IGNORE INTO candle_aux VALUES(?,?,?,?,?,?)',
                       (symbol, tf, int(raw[0]), encoded(fields), now, source))
        except (ValueError, TypeError, IndexError, KeyError) as exc:
            # Bad optional data cannot silently become zero or change a signal.
            db.execute('INSERT INTO aux_issues(ms,symbol,reason) VALUES(?,?,?)',
                       (now, symbol, str(exc)[:120]))
    db.commit()


def event(store, key, kind, event_ms, received_ms, payload):
    data = dict(payload, revision=REVISION, protocol_sha256=protocol_hash(), observational_only=True,
                source_provenance=store.provenance)
    store.db.execute('INSERT OR IGNORE INTO feature_events(key,kind,event_ms,received_ms,payload) VALUES(?,?,?,?,?)',
                     (key, kind, event_ms, received_ms, encoded(data)))
    store.db.commit()


def relative_strength(coin, btc):
    """Exact same closed 4h endpoints, 72h apart; never positional alignment."""
    if not coin or not btc:
        return None
    end = int(coin[-1][0]); start = end - 3*DAY
    c = {int(r[0]): r for r in coin}; b = {int(r[0]): r for r in btc}
    times = range(start, end+1, CFG.context_ms)
    if any(t not in c or t not in b for t in times):
        return None
    return (finite(c[end][4], True)/finite(c[start][4], True)-1 -
            (finite(b[end][4], True)/finite(b[start][4], True)-1))


def record_context(store, p, bars, context, venue):
    """No network, no mutation of p, no changes to signal IDs or candidate set."""
    key = f"{venue}:{p['symbol']}:{p['candle_ms']}:{p['config_hash']}"
    aux = store.db.execute("SELECT payload,received_ms FROM candle_aux WHERE symbol=? AND tf='30m' AND ms=?",
                           (p['symbol'], p['candle_ms'])).fetchone()
    data = dict(source_venue=venue, signal_status=p['status'], reason=p['reason'],
                direction=p.get('direction'), volume_ratio=p.get('volume_ratio'),
                taker=kline_aux([]), btc_relative_3d=None, oi_change_4h=None,
                funding_percentile_30d=None, derivatives_status='PENDING_OR_UNAVAILABLE')
    if aux and aux[1] <= p['observed_ms']:
        data['taker'] = json.loads(aux[0])
    btc = [json.loads(r[0]) for r in reversed(store.db.execute(
        "SELECT payload FROM candles WHERE symbol='BTCUSDT' AND tf='4h' AND open_ms<=? "
        'ORDER BY open_ms DESC LIMIT 120', (context[-1][0],)).fetchall())]
    data['btc_relative_3d'] = relative_strength(context, btc)
    data['btc_regime_4h']=None
    if len(btc)>=80 and btc[-1][0]==context[-1][0] and all(b[0]-a[0]==CFG.context_ms for a,b in zip(btc,btc[1:])):
        f=ema([r[4] for r in btc],20);s=ema([r[4] for r in btc],50)
        data['btc_regime_4h']=('UP' if btc[-1][4]>f[-1]>s[-1] and f[-1]>f[-4] else
                               'DOWN' if btc[-1][4]<f[-1]<s[-1] and f[-1]<f[-4] else 'SIDEWAYS')
    if p['status'] == 'SETUP':
        from .observations import context_fields
        data.update(context_fields(p, bars, context))
        data['price_change_4h'] = bars[-1][4]/bars[-9][4]-1 if len(bars) >= 9 else None
        data['price_change_24h'] = bars[-1][4]/bars[-49][4]-1 if len(bars) >= 49 else None
        sign=1 if p['direction']=='LONG' else -1
        prior=bars[-61:-1]
        level=max(r[2] for r in prior) if sign==1 else min(r[3] for r in prior)
        distance=sign*(p['reference_entry']-p['stop'])
        room=sign*(level-p['reference_entry'])
        data.update(prior_60bar_extreme=level,prior_extreme_broken=room<=0,
                    room_to_prior_extreme_r=room/distance if room>0 and distance>0 else None,
                    room_definition='PRIOR_WINDOW_EXTREME_NOT_PROVEN_SUPPORT_RESISTANCE')
    event(store, key, 'SIGNAL_CONTEXT', p['observed_ms'], p['observed_ms'], data)
    if p['status'] == 'SETUP':
        store.db.execute('INSERT OR IGNORE INTO feature_jobs VALUES(?,?,?,?,?,?)',
                         (key, p['symbol'], p['observed_ms'], p['observed_ms'], 0,
                          encoded(dict(p, source_venue=venue, remaining=['oi', 'funding'], funding_rows=[]))))
        store.db.commit()


def oi_change(rows, cutoff):
    data = {}
    for row in rows:
        stamp = int(row['timestamp']); value = finite(row['openInterest'], True)
        if stamp > cutoff:
            continue
        if stamp in data and data[stamp] != value:
            raise ValueError('OI_CONFLICT')
        data[stamp] = value
    if not data:
        return dict(status='MISSING', change_4h=None)
    last = max(data); start = last - 240*MINUTE
    if cutoff-last > 10*MINUTE or any(t not in data for t in range(start, last+1, 5*MINUTE)):
        return dict(status='STALE_OR_GAP', change_4h=None)
    return dict(status='OK', change_4h=data[last]/data[start]-1,
                first_ms=start, last_ms=last, unit='BASE_ASSET_NOT_USD_VALUE')


def funding_percentile(rows, cutoff):
    values = {}
    for row in rows:
        t = int(row['fundingTime']); rate = finite(row['fundingRate'])
        if t > cutoff or t < cutoff-30*DAY-8*60*MINUTE:
            continue
        if t in values and values[t] != rate:
            raise ValueError('FUNDING_CONFLICT')
        values[t] = rate
    ordered = sorted(values)
    if len(ordered) < 3:
        return dict(status='MISSING', percentile=None)
    gaps = {b-a for a,b in zip(ordered, ordered[1:])}
    if len(gaps) != 1 or next(iter(gaps)) not in (60*MINUTE, 240*MINUTE, 480*MINUTE):
        return dict(status='INTERVAL_CHANGE_OR_GAP', percentile=None)
    interval = next(iter(gaps)); last = ordered[-1]
    if cutoff-last > interval+5*MINUTE or ordered[0] > cutoff-30*DAY+interval:
        return dict(status='INCOMPLETE_30D_HISTORY', percentile=None)
    past = [values[t] for t in ordered if cutoff-30*DAY <= t < last]
    if not past:
        return dict(status='MISSING', percentile=None)
    current = values[last]
    # Mid-rank ties: constant funding is 50th percentile, never falsely crowded.
    rank = (sum(v < current for v in past)+.5*sum(v == current for v in past))/len(past)
    return dict(status='OK', percentile=rank, latest_rate=current, latest_ms=last,
                interval_ms=interval, samples=len(past), rate_per_8h=current*480*MINUTE/interval)


def hypothesis_results(data, direction):
    sign = 1 if direction == 'LONG' else -1
    taker = data.get('taker_buy_share'); oi = data.get('oi_change_4h')
    price = data.get('price_change_4h'); funding = data.get('funding_percentile_30d')
    relative = data.get('btc_relative_3d')
    return dict(taker=None if taker is None else taker > .55 if sign == 1 else taker < .45,
                oi=None if oi is None or price is None else oi > 0 and sign*price > 0,
                funding=None if funding is None else funding < .9 if sign == 1 else funding > .1,
                relative=None if relative is None else sign*relative > 0)


def tick(store, client, venue, stop_before_ms):
    """One optional HTTP request per call, max two/minute, below scan priority."""
    now = client.now()
    if now+15_000 >= stop_before_ms or client.gate.state()['remaining_seconds']:
        return
    if now-int(store.setting('feature_tick_ms') or 0) < 30_000:
        return
    job = store.db.execute('SELECT * FROM feature_jobs WHERE next_ms<=? ORDER BY next_ms,key LIMIT 1', (now,)).fetchone()
    if not job:
        return
    store.setting('feature_tick_ms', now)
    p = json.loads(job['payload']); kind = p['remaining'][0]
    # At least one completed 5m observation earlier; archive publication latency
    # is not inferred from the event timestamp. Receipt time is logged separately.
    cutoff = job['signal_ms']-5*MINUTE if kind == 'oi' else job['signal_ms']-CFG.publication_delay_ms
    try:
        if kind == 'oi':
            if venue == 'BINANCE':
                raw = client.get('/futures/data/openInterestHist',symbol=job['symbol'],period='5m',
                                 startTime=cutoff-250*MINUTE,endTime=cutoff,limit=60)
                rows = [dict(timestamp=r['timestamp'],openInterest=r['sumOpenInterest']) for r in raw]
            else:
                response = client.request('/v5/market/open-interest',category='linear',symbol=job['symbol'],
                                          intervalTime='5min',startTime=cutoff-250*MINUTE,endTime=cutoff,limit=60)
                raw = response['result']
                if raw.get('symbol') != job['symbol']:
                    raise ValueError('OI_SYMBOL_MISMATCH')
                rows = raw['list']
            result = oi_change(rows, cutoff)
        else:
            end = p.get('funding_end', cutoff)
            if venue == 'BINANCE':
                raw = client.get('/fapi/v1/fundingRate',symbol=job['symbol'],
                                 startTime=cutoff-31*DAY,endTime=end,limit=1000)
                if any(r.get('symbol') != job['symbol'] for r in raw):
                    raise ValueError('FUNDING_SYMBOL_MISMATCH')
                rows = raw
            else:
                response = client.request('/v5/market/funding/history',category='linear',symbol=job['symbol'],
                                          startTime=cutoff-31*DAY,endTime=end,limit=200)
                raw = response['result']
                if raw.get('category') != 'linear' or any(r.get('symbol') != job['symbol'] for r in raw['list']):
                    raise ValueError('FUNDING_SYMBOL_MISMATCH')
                rows = [dict(fundingTime=int(r['fundingRateTimestamp']),fundingRate=r['fundingRate']) for r in raw['list']]
            p['funding_rows'].extend(rows)
            earliest = min((int(r['fundingTime']) for r in rows), default=end)
            if venue == 'BYBIT' and len(rows) == 200 and earliest > cutoff-30*DAY and earliest < end:
                p['funding_end'] = earliest-1
                store.db.execute('UPDATE feature_jobs SET payload=?,next_ms=? WHERE key=?',
                                 (encoded(p), now+30_000, job['key'])); store.db.commit()
                return
            result = funding_percentile(p['funding_rows'], cutoff)
            rows = p['funding_rows']
        event(store,job['key'],kind.upper(),cutoff,client.now(),
              dict(source_venue=venue,feature=result,raw_rows=rows,
                   timing='RETROSPECTIVE_ENRICHMENT_NOT_KNOWN_AT_SIGNAL',signal_ms=job['signal_ms']))
        p['remaining'].pop(0)
        if p['remaining']:
            store.db.execute('UPDATE feature_jobs SET payload=?,next_ms=?,attempts=0 WHERE key=?',
                             (encoded(p),now+30_000,job['key']))
        else:
            store.db.execute('DELETE FROM feature_jobs WHERE key=?',(job['key'],))
        store.db.commit()
    except (ValueError, KeyError, TypeError, RuntimeError, OSError) as exc:
        attempts = job['attempts']+1
        event(store,job['key'],f'{kind.upper()}_ERROR_{attempts}',cutoff,client.now(),
              dict(reason=str(exc)[:120],source_venue=venue))
        if attempts >= 3:
            p['remaining'].pop(0)
        if p['remaining']:
            store.db.execute('UPDATE feature_jobs SET payload=?,attempts=?,next_ms=? WHERE key=?',
                             (encoded(p),0 if attempts>=3 else attempts,now+5*MINUTE,job['key']))
        else:
            store.db.execute('DELETE FROM feature_jobs WHERE key=?',(job['key'],))
        store.db.commit()


def health(store):
    return dict(revision=REVISION,protocol_sha256=protocol_hash(),used_in_signal_decisions=False,
        context_rows=store.db.execute("SELECT COUNT(*) FROM feature_events WHERE kind='SIGNAL_CONTEXT'").fetchone()[0],
        derivative_rows=store.db.execute("SELECT COUNT(*) FROM feature_events WHERE kind IN ('OI','FUNDING')").fetchone()[0],
        pending_jobs=store.db.execute('SELECT COUNT(*) FROM feature_jobs').fetchone()[0],
        candle_aux_rows=store.db.execute('SELECT COUNT(*) FROM candle_aux').fetchone()[0],
        aux_issues=store.db.execute('SELECT COUNT(*) FROM aux_issues').fetchone()[0])
