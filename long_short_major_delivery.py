"""Delivery admission and durable first eligibility; never places orders.

The rolling budget controls notification volume, not hypothetical portfolio size.
First eligibility is descriptive: a later setup is still subject to the existing
closed-candle, chase, stop and expiry checks, and is not labelled a new origin.
"""
import hashlib
import json

from long_short_major_move import CONFIG, CONTEXT_VERSIONS


def eligible_to_publish(c, p, now_ms):
    if p.get('observation_only'):
        return 'OBSERVATION_ONLY'
    prior=c.execute('SELECT created_ms FROM ideas WHERE symbol=? ORDER BY created_ms DESC LIMIT 1',
                    (p['symbol'],)).fetchone()
    if prior and now_ms-prior[0]<CONFIG['cooldown_hours']*3600000:
        return 'COOLDOWN'
    # Pending/uncertain sends consume budget too: a lost ACK must not allow spam.
    count=c.execute('''SELECT COUNT(*) FROM ideas
        WHERE COALESCE(delivered_ms,created_ms)>?''',
        (now_ms-CONFIG['notification_window_ms'],)).fetchone()[0]
    return 'NOTIFICATION_RATE_LIMIT' if count>=CONFIG['max_notifications_per_window'] else None


def attach_first_eligibility(c, p):
    if p.get('version') not in CONTEXT_VERSIONS or p.get('stage')!='READY':
        return
    now=p['observed_ms'];since=now-CONFIG['cooldown_hours']*3600000
    row=c.execute('''SELECT id,payload FROM eligibility_origins
        WHERE symbol=? AND direction=? AND observed_ms>? AND observed_ms<=?
        ORDER BY observed_ms LIMIT 1''',(p['symbol'],p['direction'],since,now)).fetchone()
    if row:
        origin=json.loads(row['payload'])
    else:
        # Seed only from actual, earlier READY quote references in the same
        # signal family. Never reconstruct an earlier quote from later candles.
        old=c.execute('''SELECT id,payload FROM research_ideas WHERE symbol=?
            AND json_extract(payload,'$.direction')=? AND created_ms>? AND created_ms<=?
            AND json_extract(payload,'$.version') IN (?,?) ORDER BY created_ms LIMIT 1''',
            (p['symbol'],p['direction'],since,now,*sorted(CONTEXT_VERSIONS))).fetchone()
        source=json.loads(old['payload']) if old else p
        origin=dict(observed_ms=source['observed_ms'],price=source['price'],
                    direction=p['direction'],model=source['model'],
                    source_version=source['version'],source_reference_id=old['id'] if old else None)
        key=hashlib.sha256(json.dumps([p['symbol'],p['direction'],origin['observed_ms']]).encode()).hexdigest()
        c.execute('INSERT OR IGNORE INTO eligibility_origins VALUES(?,?,?,?,?)',
                  (key,p['symbol'],p['direction'],origin['observed_ms'],json.dumps(origin)))
    p['first_eligibility']=origin
    p['first_eligibility_age_ms']=now-origin['observed_ms']
    sign=1 if p['direction']=='LONG' else -1
    p['move_since_first_eligibility_pct']=sign*(p['price']/origin['price']-1)*100
