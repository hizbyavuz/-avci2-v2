"""Bounded final-gate evidence. Never selects a side or relaxes a trade rule."""
import hashlib
import json
from long_short_chart_quality import entry_quality


def initialize(c):
    c.executescript('''CREATE TABLE IF NOT EXISTS dispatch_audit(
        event_key TEXT PRIMARY KEY, observed_ms INTEGER, strategy_id TEXT,
        symbol TEXT, episode_id TEXT, action TEXT, reason TEXT, payload TEXT);
        CREATE INDEX IF NOT EXISTS dispatch_audit_time ON dispatch_audit(observed_ms);''')


def note(c, p, now_ms, action, reason, episode_id=None, **details):
    # One row per identity/reason/five-minute bucket, not one row per poll.
    identity=episode_id or p.get('setup_key') or p.get('symbol')
    sid=p.get('strategy_id','UNKNOWN')
    key=hashlib.sha256(json.dumps([sid,identity,now_ms//300000,action,reason]).encode()).hexdigest()
    payload=dict(symbol=p.get('symbol'),direction=p.get('direction'),
        assessed_stage=p.get('stage'),price=p.get('price'),trigger=p.get('trigger'),
        stop=p.get('stop'),obstacle=p.get('obstacle'),ready_blockers=p.get('ready_blockers',[]),**details)
    cur=c.execute('INSERT OR IGNORE INTO dispatch_audit VALUES(?,?,?,?,?,?,?,?)',
        (key,now_ms,sid,p.get('symbol'),episode_id,action,reason,json.dumps(payload)))
    if cur.rowcount:
        print('CHART_DISPATCH_AUDIT '+json.dumps(dict(observed_ms=now_ms,
            strategy_id=sid,episode_id=episode_id,action=action,reason=reason,**payload)),flush=True)


def admission_reason(recent, p, now_ms, already_created, config):
    if recent:
        if recent['ended_ms'] is None:return 'EXISTING_ACTIVE_EPISODE'
        # A deployment must not reset the per-coin notification cooldown.
        if now_ms-recent['created_ms']<config['cooldown_ms']:
            return 'SAME_COIN_COOLDOWN'
    if already_created>=config['max_initial_notices_per_cycle']:
        return 'CYCLE_NOTICE_CAP'
    return None


def stage_reason(p, stage, price, last_close, previous_close, closed_ms, now_ms, config):
    sign=1 if p['direction']=='LONG' else -1
    if stage=='BROKEN':return 'LOCKED_INVALIDATION_REACHED'
    if stage=='OBSTACLE_REACHED':return 'LOCKED_OBSTACLE_REACHED'
    if now_ms-p['created_ms']>=config['horizon_minutes']*60000:return 'IDEA_TIME_LIMIT'
    if stage=='EXPIRED':return 'ENTRY_PRICE_OR_PAYOFF_EXPIRED'
    if stage=='READY':return 'FRESH_CLOSED_BREAK'
    if not 250<=now_ms-closed_ms<=75000:return 'CONFIRMATION_NOT_FRESH'
    if sign*(last_close-p['trigger'])<=0:return 'CLOSE_NOT_BEYOND_TRIGGER'
    if previous_close is not None and sign*(previous_close-p['trigger'])>0:
        return 'BREAK_ALREADY_HAPPENED'
    if sign*(price-p['trigger'])<0:return 'PRICE_LOST_TRIGGER'
    blockers=entry_quality(p,price,now_ms)['blockers']
    if blockers:return blockers[0]
    return 'WAITING_CONFIRMATION'
