"""Durable core-alert delivery; the event exists before any network send.

Telegram has no idempotency key for sendMessage. An interrupted/ambiguous send
is recorded UNKNOWN and is never blindly replayed. Unclaimed pending messages
can recover only within the existing freshness limit and after revalidation.
"""
import json
import sqlite3
from datetime import datetime, timezone
from long_short_telegram_delivery_integrity import policy, MAX_CLOSED_CANDLE_AGE_SECONDS


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def init(con):
    con.execute('''CREATE TABLE IF NOT EXISTS core_delivery_outbox(
        event_id INTEGER PRIMARY KEY, message TEXT NOT NULL,
        closed_candle_time TEXT, prior_visible INTEGER NOT NULL DEFAULT 0,
        state TEXT NOT NULL DEFAULT 'PENDING', claimed_at TEXT, error TEXT)''')


def enqueue(con, values, message, closed_candle_time, prior_visible=False):
    """Caller commits this together with watch_state/watch_episodes changes."""
    cursor=con.execute('''INSERT INTO events(
        event_time_utc,symbol,direction,stage_from,stage_to,price,closed_5m,
        condition_time_utc,telegram_sent_time_utc,telegram_status,telegram_error,
        delay_seconds,payload_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',values)
    event_id=cursor.lastrowid
    if message:
        con.execute('''INSERT INTO core_delivery_outbox
            (event_id,message,closed_candle_time,prior_visible) VALUES(?,?,?,?)''',
            (event_id,message,closed_candle_time,int(prior_visible)))
    return event_id


def _finish(con,event_id,status,error=None,sent_at=None):
    updated=con.execute("""UPDATE core_delivery_outbox SET state=?,error=?
        WHERE event_id=? AND state IN ('PENDING','SENDING')""",(status,error,event_id))
    if updated.rowcount!=1:
        con.commit()
        return
    con.execute('''UPDATE events SET telegram_status=?,telegram_error=?,telegram_sent_time_utc=?,
        delay_seconds=CASE WHEN ? IS NULL THEN NULL ELSE
        (julianday(?)-julianday(condition_time_utc))*86400.0 END WHERE id=?''',
        (status,error,sent_at,sent_at,sent_at,event_id))
    con.commit()


def dispatch(con,send,revalidate,at=None,event_id=None):
    fixed_clock=at is not None
    at=at or now_iso(); con.row_factory=sqlite3.Row
    # A crashed worker may have sent successfully but not persisted the ACK.
    stale=con.execute('''SELECT event_id FROM core_delivery_outbox WHERE state='SENDING'
        AND (julianday(?)-julianday(claimed_at))*86400.0>?''',
        (at,MAX_CLOSED_CANDLE_AGE_SECONDS)).fetchall()
    for row in stale:_finish(con,row['event_id'],'UNKNOWN','INTERRUPTED_SEND_NO_BLIND_RETRY')
    rows=con.execute('''SELECT e.*,o.message,o.closed_candle_time,o.prior_visible
        FROM core_delivery_outbox o JOIN events e ON e.id=o.event_id
        WHERE o.state='PENDING' AND (? IS NULL OR e.id=?) ORDER BY e.id LIMIT 24''',
        (event_id,event_id)).fetchall()
    for row in rows:
        claim=con.execute("UPDATE core_delivery_outbox SET state='SENDING',claimed_at=? WHERE event_id=? AND state='PENDING'",(at,row['id']))
        con.commit()
        if claim.rowcount!=1:continue
        guard=policy(row['stage_to'],at,row['closed_candle_time'],bool(row['prior_visible']))
        try:
            age=(datetime.fromisoformat(at)-datetime.fromisoformat(row['event_time_utc'])).total_seconds()
            if age<0 or age>MAX_CLOSED_CANDLE_AGE_SECONDS:guard={'allowed':False,'reason':'EVENT_EXPIRED'}
        except (ValueError,TypeError):guard={'allowed':False,'reason':'INVALID_EVENT_TIME'}
        if not guard['allowed']:
            _finish(con,row['id'],'SUPPRESSED',guard['reason']);continue
        try:
            check=revalidate(dict(row))
        except Exception as exc:
            check={'qualified':False,'reason':'REVALIDATION_'+type(exc).__name__}
        if not check.get('qualified'):
            _finish(con,row['id'],'SUPPRESSED',str(check.get('reason','REVALIDATION_FAILED')));continue
        payload=json.loads(row['payload_json'])
        payload['_delivery_revalidation']=check
        con.execute('UPDATE events SET payload_json=? WHERE id=?',(json.dumps(payload),row['id']))
        con.commit()
        # Revalidation may itself be slow; check freshness immediately at send.
        send_at=at if fixed_clock else now_iso()
        # Tests may provide a deterministic clock through `at`; production
        # passes none and uses a fresh clock after all network validation.
        if not policy(row['stage_to'],send_at,row['closed_candle_time'],bool(row['prior_visible']))['allowed']:
            _finish(con,row['id'],'SUPPRESSED','EXPIRED_DURING_REVALIDATION');continue
        try:
            sent_at=send(row['message'])
            if not sent_at:raise RuntimeError('MISSING_ACK')
        except Exception as exc:
            _finish(con,row['id'],'UNKNOWN','SEND_RESULT_UNCERTAIN:'+type(exc).__name__)
        else:
            _finish(con,row['id'],'SENT',sent_at=sent_at)
        print('CORE_OUTBOX_RESULT',row['symbol'],row['id'],flush=True)
