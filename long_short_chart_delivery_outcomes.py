"""Bounded outcome lane for acknowledged Telegram deliveries, not signal rules.

The original resolver shares WATCH capacity with all historical assessments.
This process reads only acknowledged deliveries and native closed candles. It
fills missing labels, preserves final labels, and never publishes or orders.
The additional 30m horizon is observational; 180m remains the primary endpoint.
Decision source files and their strategy identity are deliberately unchanged.
"""
import argparse
import json
import signal
import sqlite3
import threading
import time
from collections import Counter
from pathlib import Path

from long_short_chart_first_worker import VALID, root, path_outcome, outcome_v2

VERSION = 'DELIVERY_OUTCOME_LANE_V1_2026_10_16'
HORIZONS = (15, 30, 60, 180)
RETRY_MS = 3600000
BATCH_SIZE = 120
STOP = threading.Event()
MODERN_PREFIXES = (
    'CHART_FIRST_TWO_SETUPS_V2_', 'CHART_FIRST_FLOW_REVERSAL_V3_',
    'CHART_FIRST_PATH_GUARD_V3_1_', 'CHART_FIRST_RELIABILITY_V3_2_',
    'CHART_FIRST_RELIABILITY_V3_3_', 'CHART_FIRST_STRUCTURE_V4_',
)


def due_jobs(c, now_ms, limit=BATCH_SIZE):
    """An old rejected scan cannot consume a delivered-message slot."""
    return c.execute("""WITH horizons(h) AS (VALUES(15),(30),(60),(180))
        SELECT a.*, h.h AS horizon FROM assessments a CROSS JOIN horizons h
        LEFT JOIN outcomes o ON o.assessment_id=a.id AND o.horizon=h.h
        WHERE a.stage IN ('DELIVERED_READY','DELIVERED_WATCH')
          AND a.reason='TELEGRAM_ACK' AND a.observed_ms+(h.h+1)*60000<=?
          AND (o.assessment_id IS NULL OR
               (o.state NOT IN ('RESOLVED','UNRESOLVED_BOUNDARY') AND o.checked_ms<=?))
        ORDER BY (o.assessment_id IS NOT NULL), a.observed_ms, a.id, h.h LIMIT ?
        """, (now_ms, now_ms-RETRY_MS, limit)).fetchall()


def measure(p, kdb, horizon, now_ms):
    modern = (p.get('outcome_schema') == 'PARTIAL_MINUTE_V2' or
              p.get('version', '').startswith(MODERN_PREFIXES))
    start = (p['observed_ms']//60000 + (0 if modern else 1))*60000
    end = (((p['observed_ms']+horizon*60000+59999)//60000)*60000
           if modern else start+horizon*60000)
    if end > now_ms:
        raise ValueError('HORIZON_NOT_CLOSED')
    bars = kdb.execute("""SELECT open_ms,open,high,low,close,volume,close_ms
        FROM futures_closed_klines WHERE symbol=? AND interval='1m'
          AND open_ms>=? AND open_ms<? AND source IN (?,?,?) ORDER BY open_ms
        """, (p['symbol'], start, end, *sorted(VALID))).fetchall()
    cost = p.get('cost_assumption_pct', p.get('geometry', {}).get('cost_assumption_pct', .30))
    return (outcome_v2(p, bars, horizon, cost) if modern
            else path_outcome(p, bars, start, horizon))


def evidence(row, p, state, result):
    """Features come only from the saved delivery, never from a later scan."""
    fields = ('version', 'strategy_id', 'episode_id', 'symbol', 'direction',
              'stage', 'observed_ms', 'price', 'model', 'scenario', 'trigger',
              'stop', 'obstacle', 'entry_limit', 'geometry', 'hourly_flow',
              'volume_5m_multiple', 'volume_15m_multiple', 'support')
    saved = {key: p[key] for key in fields if key in p}
    saved['context_directions'] = {
        tf: v.get('direction') for tf, v in (p.get('context') or {}).items()
        if isinstance(v, dict)
    }
    return dict(measurement_version=VERSION, assessment_id=row['id'],
                horizon=row['horizon'], state=state, signal=saved, outcome=result)


def run_batch(c, kdb, now_ms, emit=None, limit=BATCH_SIZE):
    jobs = due_jobs(c, now_ms, limit)
    counts = Counter(selected=len(jobs))
    for row in jobs:
        p = {}
        try:
            p = json.loads(row['payload'])
            if not isinstance(p, dict):
                raise ValueError('INVALID_DELIVERY_PAYLOAD')
            # Bind identity and reference price to the original database row.
            p.update(symbol=row['symbol'], direction=row['direction'], stage=row['stage'],
                     observed_ms=row['observed_ms'], price=row['price'])
            result = measure(p, kdb, row['horizon'], now_ms)
            state = result.get('first_barrier_status', 'RESOLVED')
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            state, result = 'UNRESOLVED', {'reason': str(exc)[:160]}
            if not isinstance(p, dict):
                p = {}
        # Native resolver may have completed the same job since selection.
        # Commit each small result independently; never hold a write lock while
        # reading candle paths, and never replace a final or freshly retried row.
        try:
            changed = c.execute("""INSERT INTO outcomes VALUES(?,?,?,?,?)
                ON CONFLICT(assessment_id,horizon) DO UPDATE SET
                    state=excluded.state,checked_ms=excluded.checked_ms,payload=excluded.payload
                WHERE outcomes.state NOT IN ('RESOLVED','UNRESOLVED_BOUNDARY')
                  AND outcomes.checked_ms<=?""",
                (row['id'], row['horizon'], state, now_ms,
                 json.dumps(result), now_ms-RETRY_MS)).rowcount
            c.commit()
        except sqlite3.Error:
            c.rollback()
            raise
        if changed:
            counts[state] += 1
            if emit:
                emit(evidence(row, p, state, result))
        else:
            counts['concurrent_completion_or_retry'] += 1
    return dict(counts)


def coverage(c, now_ms):
    """Count unknown outcomes explicitly, including each immature horizon."""
    return [dict(r) for r in c.execute("""WITH horizons(h) AS (VALUES(15),(30),(60),(180))
        SELECT h.h AS horizon, a.stage, COUNT(*) AS records,
          COALESCE(SUM(o.state='RESOLVED'),0) AS resolved,
          COALESCE(SUM(o.state='UNRESOLVED_BOUNDARY'),0) AS boundary_ambiguous,
          SUM(o.state IS NOT NULL AND o.state NOT IN ('RESOLVED','UNRESOLVED_BOUNDARY')) AS data_unresolved,
          SUM(o.assessment_id IS NULL AND a.observed_ms+(h.h+1)*60000>?) AS pending,
          SUM(o.assessment_id IS NULL AND a.observed_ms+(h.h+1)*60000<=?) AS missing_due
        FROM assessments a CROSS JOIN horizons h LEFT JOIN outcomes o
          ON o.assessment_id=a.id AND o.horizon=h.h
        WHERE a.stage IN ('DELIVERED_READY','DELIVERED_WATCH') AND a.reason='TELEGRAM_ACK'
        GROUP BY h.h,a.stage ORDER BY h.h,a.stage""", (now_ms, now_ms))]


def log_evidence(value):
    print('CHART_DELIVERY_MEASURED '+json.dumps(value, ensure_ascii=False), flush=True)


def export_completed(c, now_ms, emit, limit=2000):
    """Bounded read-only replay so older READY results also expose their features.

Consumers deduplicate by (assessment_id,horizon); startup replay is explicitly
marked and is never an additional observation or a re-evaluation of a label.
"""
    rows = c.execute("""SELECT a.*,o.horizon,o.state,o.payload AS result_payload
        FROM assessments a JOIN outcomes o ON o.assessment_id=a.id
        WHERE a.stage IN ('DELIVERED_READY','DELIVERED_WATCH') AND a.reason='TELEGRAM_ACK'
          AND a.observed_ms>=? AND a.observed_ms<=?
          AND o.state IN ('RESOLVED','UNRESOLVED_BOUNDARY')
        ORDER BY a.observed_ms DESC,a.id DESC,o.horizon LIMIT ?""",
        (now_ms-48*3600000, now_ms, limit+1)).fetchall()
    invalid = 0
    for row in rows[:limit]:
        try:
            p, result = json.loads(row['payload']), json.loads(row['result_payload'])
            value = evidence(row, p, row['state'], result)
        except (ValueError, TypeError, AttributeError):
            invalid += 1
            continue
        value['startup_replay'] = True
        emit(value)
    return dict(exported=min(len(rows),limit)-invalid, invalid=invalid,
                truncated=len(rows)>limit, window_hours=48,
                deduplicate_by=['assessment_id','horizon'])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: STOP.set())
    print('CHART_DELIVERY_OUTCOME_START '+json.dumps(dict(version=VERSION,
        horizons=HORIZONS, primary_horizon=180, batch_size=BATCH_SIZE,
        telegram_publish=False, automatic_orders=False)), flush=True)
    last_health = 0
    replayed = False
    while not STOP.is_set():
        c = kdb = None
        try:
            base = Path(root()).resolve()
            c = sqlite3.connect((base/'chart_first_v1.db').as_uri()+'?mode=rw', uri=True, timeout=3)
            c.row_factory = sqlite3.Row
            c.execute("""CREATE INDEX IF NOT EXISTS assessments_delivered_outcome_queue
                ON assessments(observed_ms,id) WHERE
                stage IN ('DELIVERED_READY','DELIVERED_WATCH') AND reason='TELEGRAM_ACK'""")
            c.commit()
            kdb = sqlite3.connect((base/'binance_futures_kline_shadow.db').as_uri()+'?mode=ro', uri=True, timeout=3)
            now_ms = int(time.time()*1000)
            if not replayed:
                print('CHART_DELIVERY_EXPORT '+json.dumps(export_completed(c, now_ms, log_evidence)), flush=True)
                replayed = True
            counts = run_batch(c, kdb, now_ms, emit=log_evidence)
            if counts['selected'] or now_ms-last_health >= 300000:
                print('CHART_DELIVERY_OUTCOME_PROGRESS '+json.dumps(dict(
                    version=VERSION, observed_ms=now_ms, batch=counts,
                    coverage=coverage(c, now_ms))), flush=True)
                last_health = now_ms
        except (sqlite3.Error, OSError) as exc:
            print('CHART_DELIVERY_OUTCOME_ERROR '+type(exc).__name__+' '+str(exc)[:160], flush=True)
            if args.once:
                raise
        finally:
            if kdb is not None: kdb.close()
            if c is not None: c.close()
        if args.once: return
        STOP.wait(10)


if __name__ == '__main__':
    main()
