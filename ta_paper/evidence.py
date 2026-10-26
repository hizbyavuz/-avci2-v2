"""Observation-only evidence. No strategy import, network, orders or Telegram.

Technical barrier outcomes are not executable fills. Full net expectancy stays
unverified until same-venue entry/exit liquidity and settled funding are known.
"""
from contextlib import contextmanager
import json
import math
from pathlib import Path
import sqlite3

MINUTE = 60_000
DAY = 1440 * MINUTE
REVISION = 'FROZEN_OBSERVATIONS_20261027_1'
HORIZONS = (60, 240, 1440, 4320)


def number(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('NON_FINITE_EVIDENCE')
    return value


def dumps(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


@contextmanager
def readonly(path):
    db = sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True, timeout=.2)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    try:yield db
    finally:db.close()


class Evidence:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=.5)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
          PRAGMA journal_mode=WAL;
          PRAGMA synchronous=FULL;
          CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
          CREATE TABLE IF NOT EXISTS samples(venue TEXT,symbol TEXT,ms INTEGER,received_ms INTEGER,payload TEXT,
            PRIMARY KEY(venue,symbol,ms));
          CREATE TABLE IF NOT EXISTS flows(symbol TEXT,ms INTEGER,buy_quote REAL,sell_quote REAL,
            trades INTEGER,complete INTEGER,received_ms INTEGER,PRIMARY KEY(symbol,ms));
          CREATE TABLE IF NOT EXISTS bars(symbol TEXT,ms INTEGER,payload TEXT,received_ms INTEGER,
            PRIMARY KEY(symbol,ms));
          CREATE TABLE IF NOT EXISTS candidates(id TEXT PRIMARY KEY,symbol TEXT,anchor_ms INTEGER,
            delivery_ms INTEGER,payload TEXT,features TEXT,result TEXT);
          CREATE TABLE IF NOT EXISTS features(id TEXT PRIMARY KEY,ms INTEGER,symbol TEXT,role TEXT,payload TEXT);
          CREATE TABLE IF NOT EXISTS issues(id INTEGER PRIMARY KEY,ms INTEGER,reason TEXT,payload TEXT);
          CREATE TABLE IF NOT EXISTS funding(symbol TEXT,ms INTEGER,rate REAL,received_ms INTEGER,
            PRIMARY KEY(symbol,ms));
          CREATE INDEX IF NOT EXISTS candidates_anchor ON candidates(anchor_ms);
          CREATE INDEX IF NOT EXISTS features_ms ON features(ms);
        ''')
        self.db.execute('INSERT OR IGNORE INTO meta VALUES(?,?)', ('revision', REVISION))
        self.db.commit()

    def issue(self, now, reason, **fields):
        self.db.execute('INSERT INTO issues(ms,reason,payload) VALUES(?,?,?)', (now,reason,dumps(fields)))
        self.db.commit()

    def sample(self, symbol, ms, received, payload):
        if ms > received+1000 or received-ms > 5000:
            raise ValueError('STALE_OR_FUTURE_TICKER')
        self.db.execute('INSERT OR IGNORE INTO samples VALUES(?,?,?,?,?)',
                        ('BYBIT',symbol,ms,received,dumps(payload)))

    def bar(self, symbol, row, received):
        row = [int(row[0]), *[number(v) for v in row[1:6]]]
        if (row[0] % MINUTE or row[0]+MINUTE > received+1000 or
                row[3] <= 0 or row[2] < max(row[1],row[4],row[3]) or
                row[3] > min(row[1],row[4]) or row[5] < 0):
            raise ValueError('INVALID_OBSERVATION_BAR')
        old = self.db.execute('SELECT payload FROM bars WHERE symbol=? AND ms=?', (symbol,row[0])).fetchone()
        if old and old[0] != dumps(row):
            self.issue(received,'OBSERVATION_CANDLE_CHANGED',symbol=symbol,ms=row[0])
            raise ValueError('OBSERVATION_CANDLE_CHANGED')
        self.db.execute('INSERT OR IGNORE INTO bars VALUES(?,?,?,?)', (symbol,row[0],dumps(row),received))

    def asof(self, symbol, at, age=5*MINUTE):
        row = self.db.execute('SELECT * FROM samples WHERE venue=? AND symbol=? AND ms<=? '
                              'AND received_ms<=? AND ms>=? ORDER BY ms DESC LIMIT 1',
                              ('BYBIT',symbol,at,at,at-age)).fetchone()
        return json.loads(row['payload']) if row else None

    def market_features(self, symbol, at, candle_ms, source=None):
        current = self.asof(symbol,at)
        previous = self.asof(symbol,at-240*MINUTE)
        features = dict(observation_revision=REVISION,source_venue='BYBIT',asof_ms=at,
                        oi_change_4h=None,taker_buy_share_30m=None,funding_rate=None,
                        funding_percentile_30d=None,mark_index_premium=None,
                        btc_return_4h=None,return_4h=None,relative_return_3d=None,missing=[],
                        funding_distribution_source='LIVE_INDICATIVE_HOURLY_SAMPLES',
                        historical_feature_parity_verified=False)
        if current:
            features['ticker'] = current
            features['funding_rate'] = current.get('fundingRate')
            mark,index = current.get('markPrice'),current.get('indexPrice')
            if mark and index:
                features['mark_index_premium'] = mark/index-1
            oi,old = current.get('openInterest'),(previous or {}).get('openInterest')
            if oi is not None and old and current.get('oi_received_ms',0)>=at-5*MINUTE:
                features['oi_change_4h'] = oi/old-1
        flow = self.db.execute('SELECT * FROM flows WHERE symbol=? AND ms>=? AND ms<? '
                               'AND received_ms<=? ORDER BY ms',
                               (symbol,candle_ms,candle_ms+30*MINUTE,at)).fetchall()
        if (len(flow)==30 and all(r['ms']==candle_ms+i*MINUTE and r['complete'] for i,r in enumerate(flow))):
            buy=sum(r['buy_quote'] for r in flow);sell=sum(r['sell_quote'] for r in flow)
            if buy+sell>0:features['taker_buy_share_30m']=buy/(buy+sell)
        # Indicative funding is sampled once per minute; never substitute it for
        # a historical settlement. Require a full 30-day history before ranks.
        rates=self.db.execute('SELECT MAX(ms) AS ms,payload FROM samples WHERE symbol=? AND ms>=? AND ms<? '
                              'AND received_ms<=? GROUP BY ms/3600000 ORDER BY ms', (symbol,at-30*DAY,at,at)).fetchall()
        hourly={r['ms']//(60*MINUTE):json.loads(r['payload']).get('fundingRate') for r in rates}
        values=[v for v in hourly.values() if v is not None]
        if len(values)>=684 and rates and rates[0]['ms']<=at-30*DAY+60*MINUTE and features['funding_rate'] is not None:
            features['funding_percentile_30d']=sum(v<features['funding_rate'] for v in values)/len(values)
        if source is not None:
            def closes(s):
                rows=source.execute("SELECT payload FROM candles WHERE symbol=? AND tf='4h' AND open_ms+?<=? "
                                    'ORDER BY open_ms DESC LIMIT 19',(s,240*MINUTE,at-2000)).fetchall()
                bars=[json.loads(r[0]) for r in reversed(rows)]
                latest=((at-2000)//(240*MINUTE)-1)*240*MINUTE
                return bars if bars and bars[-1][0]==latest else []
            btc,coin=closes('BTCUSDT'),closes(symbol)
            if len(coin)>=2 and coin[-1][0]-coin[-2][0]==240*MINUTE:
                features['return_4h']=number(coin[-1][4])/number(coin[-2][4])-1
            if len(btc)>=2 and btc[-1][0]-btc[-2][0]==240*MINUTE:
                features['btc_return_4h']=number(btc[-1][4])/number(btc[-2][4])-1
            if (len(btc)==len(coin)==19 and all(b[0]==c[0] for b,c in zip(btc,coin)) and
                    all(btc[i][0]-btc[i-1][0]==240*MINUTE for i in range(1,19))):
                features['relative_return_3d']=(number(coin[-1][4])/number(coin[0][4])-1)-(
                                               number(btc[-1][4])/number(btc[0][4])-1)
        for field in ('oi_change_4h','taker_buy_share_30m','funding_rate','funding_percentile_30d',
                      'mark_index_premium','btc_return_4h','relative_return_3d'):
            if features[field] is None:features['missing'].append(field)
        return features

    def register(self, source, now):
        # Old notifications lack delivery timestamps. They remain in the source
        # database, explicitly excluded from the prospective cohort.
        columns={r[1] for r in source.execute('PRAGMA table_info(outbox)')}
        if 'delivered_at_ms' not in columns:return 0
        rows=source.execute("SELECT w.*,o.delivered_at_ms FROM watch_candidates w JOIN outbox o ON o.key='watch:'||w.id "
                            "WHERE o.state='DELIVERED' AND o.delivered_at_ms IS NOT NULL "
                            'AND w.created_ms>=? ORDER BY w.created_ms DESC LIMIT 1000', (now-4*DAY,)).fetchall()
        registered=0
        for row in rows:
            if self.db.execute('SELECT 1 FROM candidates WHERE id=?',(row['id'],)).fetchone():continue
            p=json.loads(row['payload'])
            if p.get('source_venue')!='BYBIT':continue
            delivered=int(row['delivered_at_ms'])
            anchor=((delivered+2000)//MINUTE+1)*MINUTE
            fields=self.market_features(row['symbol'],int(p['observed_ms']),int(p['candle_ms']),source)
            payload=dict(p,observation_only=True,entry_model='FIRST_FULL_MINUTE_AFTER_TELEGRAM_ACK_PLUS_2S',
                         executable_fill_verified=False,signal_rules_changed=False)
            self.db.execute('INSERT OR IGNORE INTO candidates VALUES(?,?,?,?,?,?,?)',
                            (row['id'],row['symbol'],anchor,delivered,dumps(payload),dumps(fields),'{}'))
            registered+=1
        # Keep rejected peers as well. These are descriptive controls, not an
        # independently randomized trial or retrospectively selected winners.
        for row in source.execute('SELECT * FROM assessments WHERE ms>=? ORDER BY ms DESC LIMIT 2000',(now-35*MINUTE,)):
            if self.db.execute('SELECT 1 FROM features WHERE id=?',(row['key'],)).fetchone():continue
            p=json.loads(row['payload'])
            fields=self.market_features(row['symbol'],row['ms'],p['candle_ms'],source)
            fields.update(assessment=p,classification='CANDIDATE' if p['status']=='SETUP' else 'REJECTED_PEER')
            self.db.execute('INSERT OR IGNORE INTO features VALUES(?,?,?,?,?)',
                            (row['key'],row['ms'],row['symbol'],fields['classification'],dumps(fields)))
        self.db.commit()
        return registered

    def evaluate(self, now):
        changed=0
        for candidate in self.db.execute("SELECT * FROM candidates WHERE json_extract(result,'$.horizon_finalized') IS NOT 1").fetchall():
            p=json.loads(candidate['payload']);start=candidate['anchor_ms'];symbol=candidate['symbol']
            end=min(start+3*DAY,(now-2000)//MINUTE*MINUTE)
            rows=[json.loads(r[0]) for r in self.db.execute('SELECT payload FROM bars WHERE symbol=? '
                   'AND ms>=? AND ms<? ORDER BY ms',(symbol,start,end))]
            result=path_result(p,rows,start,end,now)
            result['features_missing']=json.loads(candidate['features'])['missing']
            # Settlements are recorded separately. No fabricated zero funding.
            result['settled_funding_rows']=self.db.execute('SELECT COUNT(*) FROM funding WHERE symbol=? '
                'AND ms>=? AND ms<=?',(symbol,start,result.get('exit_ms') or end)).fetchone()[0]
            covered=self.db.execute('SELECT value FROM meta WHERE key=?',('funding:'+candidate['id'],)).fetchone()
            if result.get('first_barrier') and covered and int(covered[0])>=result['exit_ms']:
                settlements=self.db.execute('SELECT ms,rate FROM funding WHERE symbol=? AND ms>=? AND ms<=?',
                                           (symbol,start,result['exit_ms'])).fetchall()
                cost=0.;missing_mark=False;sign=1 if p['direction']=='LONG' else -1
                for settlement in settlements:
                    if settlement['ms']==start or settlement['ms']>=result['exit_ms']-MINUTE:
                        missing_mark=True;result['funding_timing_ambiguous']=True;break
                    sample=self.asof(symbol,settlement['ms'],90_000)
                    if not sample or not sample.get('markPrice'):missing_mark=True;break
                    cost+=sign*sample['markPrice']*settlement['rate']
                if not missing_mark:
                    result.update(model_net_r=result['model_r_before_funding']-cost/result['reference_risk'],
                        model_net_r_1_5=result['model_r_before_funding_1_5']-cost/result['reference_risk'],
                        funding_model='SETTLED_RATE_WITH_PRECEDING_MARK_PROXY_MAX_90S',funding_cost_per_unit=cost)
                else:result['funding_status']='TIMING_OR_MARK_UNRESOLVED'
            encoded=dumps(result)
            if encoded!=candidate['result']:
                old=json.loads(candidate['result'])
                if (old.get('first_barrier')!=result.get('first_barrier') or
                        set(old.get('checkpoints',{}))!=set(result['checkpoints']) or
                        ('model_net_r' not in old and 'model_net_r' in result) or
                        old.get('status')!=result['status']):
                    print('TA_EVIDENCE_RESULT '+dumps(dict(candidate_id=candidate['id'],symbol=symbol,
                        direction=p['direction'],delivery_ms=candidate['delivery_ms'],result=result)),flush=True)
                self.db.execute('UPDATE candidates SET result=? WHERE id=?',(encoded,candidate['id']))
                changed+=1
        self.db.commit()
        return changed

    def summary(self):
        results=[json.loads(r[0]) for r in self.db.execute('SELECT result FROM candidates')]
        counts={}
        for r in results:
            key=r.get('status','WAITING');counts[key]=counts.get(key,0)+1
        return dict(revision=REVISION,candidates=len(results),states=counts,
                    feature_rows=self.db.execute('SELECT count(*) FROM features').fetchone()[0],
                    samples=self.db.execute('SELECT count(*) FROM samples').fetchone()[0],
                    bars=self.db.execute('SELECT count(*) FROM bars').fetchone()[0],
                    flow_minutes=self.db.execute('SELECT count(*) FROM flows').fetchone()[0],
                    full_net_performance_verified=False,signal_rules_changed=False)


def path_result(p, rows, start, end, now):
    """Fixed reference experiment. All barriers use post-notification bars only."""
    result=dict(status='WAITING',source_venue='BYBIT',entry_anchor_ms=start,
                model='TECHNICAL_BARRIERS_NOT_EXECUTABLE_FILLS',net_r=None,
                net_status='UNVERIFIED_LIQUIDITY_AND_FUNDING',checkpoints={},
                first_barrier=None,exit_ms=None,coverage_complete=False,horizon_finalized=end>=start+3*DAY)
    if end<=start:return result
    expected=(end-start)//MINUTE
    indexed={int(r[0]):r for r in rows}
    first=indexed.get(start)
    if not first:
        result['status']='UNRESOLVED_DATA';result['missing_entry_bar']=True;return result
    sign=1 if p['direction']=='LONG' else -1
    ref=number(first[1]);stop=number(p['stop']);target=number(p['target'])
    # Preserve the signalled chase boundary; no new entry rules in the scanner.
    if sign*(ref-number(p['entry_limit']))>0 or sign*(ref-stop)<=0 or sign*(target-ref)<=0:
        result['status']='NO_MODEL_ENTRY';return result
    risk=sign*(ref-stop)
    result.update(reference_entry=ref,reference_risk=risk)
    favorable=adverse=0.;continuous=True;seen=0
    for at in range(start,end,MINUTE):
        row=indexed.get(at)
        if row is None:continuous=False;continue
        if not continuous:continue  # Later highs cannot repair an unknown first hit.
        seen+=1;o,h,l,c=map(number,row[1:5])
        favorable=max(favorable,sign*((h if sign==1 else l)-ref)/ref)
        adverse=min(adverse,sign*((l if sign==1 else h)-ref)/ref)
        if result['first_barrier'] is None:
            stop_hit=l<=stop if sign==1 else h>=stop
            target_hit=h>=target if sign==1 else l<=target
            if stop_hit or target_hit:
                barrier='STOP' if stop_hit else 'TARGET'
                exit_ref=(min(o,stop) if sign==1 else max(o,stop)) if stop_hit else target
                # Same-minute ambiguity is pessimistically stop-first. These are
                # illustrative costs, not a claim of fills/actual funding paid.
                entry=ref*(1+sign*.0002)
                exit_price=exit_ref*(1-sign*(.001 if stop_hit else .0002))
                result.update(first_barrier=barrier,exit_ms=at+MINUTE,
                              same_minute_ambiguous=bool(stop_hit and target_hit),
                              gross_reference_r=sign*(exit_ref-ref)/risk,
                              model_r_before_funding=(sign*(exit_price-entry)-.0005*(entry+exit_price))/risk)
                stressed_entry=ref*(1+sign*.0002*1.5)
                stressed_exit=exit_ref*(1-sign*(.001 if stop_hit else .0002)*1.5)
                result['model_r_before_funding_1_5']=(sign*(stressed_exit-stressed_entry)-
                                                      .0005*1.5*(stressed_entry+stressed_exit))/risk
        elapsed=(at+MINUTE-start)//MINUTE
        if elapsed in HORIZONS:
            result['checkpoints'][str(elapsed)]=dict(directional_return=sign*(c/ref-1),
                                                    max_favorable=favorable,max_adverse=adverse)
    result.update(coverage_complete=continuous,covered_minutes=seen,expected_minutes=expected,
                  max_favorable=favorable,max_adverse=adverse)
    result['path_status']='COMPLETE_TO_NOW' if continuous else 'MISSING_DATA'
    result['status']=result['first_barrier'] or ('UNRESOLVED_DATA' if not continuous else
                      'HORIZON_OPEN' if end>=start+3*DAY else 'TRACKING')
    return result


def collect_funding(path, client):
    """One bounded history request per five minutes, using the scanner's gate.

    Called only outside its decision window. Never requests Binance or changes
    candidate rules. No pagination is needed for <=72h; a full page is rejected.
    """
    if not Path(path).exists():return 0
    now=client.now()
    if client.gate.state()['remaining_seconds']:return 0
    with sqlite3.connect(path,timeout=.2) as db:
        db.row_factory=sqlite3.Row
        last=db.execute("SELECT value FROM meta WHERE key='funding_request_ms'").fetchone()
        if last and now-int(last[0])<5*MINUTE:return 0
        row=db.execute("SELECT c.*,COALESCE(CAST(a.value AS INTEGER),0) AS funding_attempts "
            "FROM candidates c LEFT JOIN meta m ON m.key='funding:'||c.id "
            "LEFT JOIN meta a ON a.key='funding_attempts:'||c.id "
            "WHERE json_extract(c.result,'$.first_barrier') IS NOT NULL "
            "AND json_extract(c.result,'$.exit_ms')<? AND m.key IS NULL "
            "AND COALESCE(CAST(a.value AS INTEGER),0)<3 ORDER BY funding_attempts,c.anchor_ms LIMIT 1",
            (now-5*MINUTE,)).fetchone()
        if not row:return 0
        db.execute("INSERT OR REPLACE INTO meta VALUES('funding_request_ms',?)",(str(now),));db.commit()
        db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',
                   ('funding_attempts:'+row['id'],str(row['funding_attempts']+1)));db.commit()
        result=json.loads(row['result']);start=row['anchor_ms'];end=result['exit_ms']
        payload=client.request('/v5/market/funding/history',category='linear',symbol=row['symbol'],
                               startTime=start,endTime=end,limit=200)
        response=payload['result'];settlements=response['list']
        if response.get('category')!='linear' or len(settlements)>=200:raise ValueError('FUNDING_HISTORY_INCOMPLETE')
        verified=[];seen=set()
        for item in settlements:
            ms=int(item['fundingRateTimestamp']);rate=number(item['fundingRate'])
            if item['symbol']!=row['symbol'] or not start<=ms<=end or ms in seen:
                raise ValueError('FUNDING_HISTORY_INVALID')
            seen.add(ms);verified.append((row['symbol'],ms,rate,now))
        db.executemany('INSERT OR IGNORE INTO funding VALUES(?,?,?,?)',verified)
        db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('funding:'+row['id'],str(end)))
        db.commit()
    return 1
