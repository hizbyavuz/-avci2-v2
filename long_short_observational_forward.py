"""Observation-only forward tracking for live stage events.

No signal gates, Telegram policy or execution rules are changed.
Each event is evaluated against its own immutable observed price and direction.
"""
import json
import sqlite3
from datetime import datetime, timezone, timedelta

HORIZONS=(15,30,60,180)
STAGES=("APPROACHING","CLOSE_CONFIRMED","EARLY:LONG","EARLY:SHORT")
def init_observational_forward(con):
    con.execute("""CREATE TABLE IF NOT EXISTS observational_forward (
        event_id INTEGER NOT NULL,
        horizon_min INTEGER NOT NULL,
        symbol TEXT NOT NULL,
        direction TEXT NOT NULL,
        stage TEXT NOT NULL,
        event_time_utc TEXT NOT NULL,
        entry_price REAL NOT NULL,
        observed_at_utc TEXT NOT NULL,
        observed_price REAL NOT NULL,
        signed_return_pct REAL NOT NULL,
        PRIMARY KEY(event_id,horizon_min)
    )""")
    con.execute("""CREATE INDEX IF NOT EXISTS ix_observational_forward_stage
        ON observational_forward(stage,horizon_min)""")

def resolve_observational_forward(db_path, fetch_prices, now=None, limit=30):
    """fetch_prices(symbol, event_time_utc, horizons, venue) -> {horizon: (timestamp, price)}.

    Fetcher must provide venue-consistent historical *closed* prices, not current tickers.
    Missing prices remain pending; no fabricated zero-return observations.
    """
    now=now or datetime.now(timezone.utc)
    with sqlite3.connect(db_path,timeout=15) as con:
        con.row_factory=sqlite3.Row
        init_observational_forward(con)
        placeholders=",".join("?" for _ in STAGES)
        events=con.execute(f"""SELECT e.id,e.symbol,e.direction,e.stage_to,
            e.event_time_utc,e.price FROM events e
            WHERE e.stage_to IN ({placeholders}) AND e.price>0
              AND e.direction IN ('LONG','SHORT')
              AND json_valid(e.payload_json)
              AND json_extract(e.payload_json,'$._live_price_source') IN ('BINANCE_SPOT','GATE_FUTURES','BYBIT_LINEAR')
              AND e.event_time_utc<=?
              AND (SELECT COUNT(*) FROM observational_forward o
                   WHERE o.event_id=e.id)<4
            ORDER BY e.id LIMIT ?""",
            (*STAGES,(now-timedelta(minutes=15)).isoformat(),limit)).fetchall()
        inserted=0; errors=0
        for ev in events:
            try:
                start=datetime.fromisoformat(ev["event_time_utc"].replace("Z","+00:00"))
                if start.tzinfo is None: start=start.replace(tzinfo=timezone.utc)
                due=[h for h in HORIZONS if start+timedelta(minutes=h)<=now]
                existing={r[0] for r in con.execute(
                    "SELECT horizon_min FROM observational_forward WHERE event_id=?",(ev["id"],))}
                due=[h for h in due if h not in existing]
                if not due: continue
                venue=json.loads(con.execute("SELECT payload_json FROM events WHERE id=?",(ev["id"],)).fetchone()[0])["_live_price_source"]
                prices=fetch_prices(ev["symbol"],ev["event_time_utc"],due,venue)
                for h in due:
                    if h not in prices: continue
                    stamp,price=prices[h]
                    price=float(price);entry=float(ev["price"])
                    if price<=0 or entry<=0: continue
                    observed=datetime.fromisoformat(str(stamp).replace("Z","+00:00"))
                    if observed.tzinfo is None: observed=observed.replace(tzinfo=timezone.utc)
                    target=start+timedelta(minutes=h)
                    if observed<target or observed>now: continue
                    signed=(price/entry-1)*100*(1 if ev["direction"]=="LONG" else -1)
                    con.execute("""INSERT OR IGNORE INTO observational_forward
                      (event_id,horizon_min,symbol,direction,stage,event_time_utc,
                       entry_price,observed_at_utc,observed_price,signed_return_pct)
                      VALUES(?,?,?,?,?,?,?,?,?,?)""",
                      (ev["id"],h,ev["symbol"],ev["direction"],ev["stage_to"],
                       ev["event_time_utc"],entry,observed.isoformat(),price,signed))
                    inserted+=1
            except (ValueError,TypeError,KeyError,sqlite3.Error) as exc:
                errors+=1
                print("OBS_FORWARD_DATA_ISSUE",ev["id"],type(exc).__name__,str(exc)[:120],flush=True)
        con.commit()
        summary=[dict(stage=r[0],horizon_min=r[1],count=r[2],
                      correct=r[3],wrong=r[4],flat=r[5],avg_signed_pct=r[6])
                 for r in con.execute("""SELECT stage,horizon_min,COUNT(*),
                   SUM(CASE WHEN signed_return_pct>0 THEN 1 ELSE 0 END),
                   SUM(CASE WHEN signed_return_pct<0 THEN 1 ELSE 0 END),
                   SUM(CASE WHEN signed_return_pct=0 THEN 1 ELSE 0 END),
                   AVG(signed_return_pct) FROM observational_forward
                   GROUP BY stage,horizon_min ORDER BY stage,horizon_min""")]
        result={"inserted":inserted,"data_issues":errors,"summary":summary}
        # Audit all preserved observations since the user-requested 14:09 Turkey time.
        # Count one observation per coin/direction/stage/UTC day/horizon,
        # so repeated alerts cannot masquerade as independent wins.
        rollout_start="2026-10-10T11:09:00+00:00"  # 14:09 Europe/Istanbul
        # Diagnose empty cohorts without assuming that no raw events were saved.
        raw_since=con.execute("""SELECT COUNT(*),COUNT(DISTINCT symbol),
            MIN(event_time_utc),MAX(event_time_utc)
            FROM events WHERE julianday(event_time_utc)>=julianday(?)""",
            (rollout_start,)).fetchone()
        eligible_since=con.execute(f"""SELECT COUNT(*),COUNT(DISTINCT e.symbol)
            FROM events e WHERE julianday(e.event_time_utc)>=julianday(?)
              AND e.stage_to IN ({placeholders}) AND e.price>0
              AND e.direction IN ('LONG','SHORT')
              AND json_valid(e.payload_json)
              AND json_extract(e.payload_json,'$._live_price_source')
                  IN ('BINANCE_SPOT','GATE_FUTURES','BYBIT_LINEAR')""",
            (rollout_start,*STAGES)).fetchone()
        observed_since=con.execute("""SELECT COUNT(DISTINCT event_id),COUNT(*)
            FROM observational_forward WHERE julianday(event_time_utc)>=julianday(?)""",
            (rollout_start,)).fetchone()
        print("OBS_FORWARD_SINCE_1409_DIAGNOSTIC",json.dumps({
            "raw_events":raw_since[0],"raw_symbols":raw_since[1],
            "first_raw_utc":raw_since[2],"last_raw_utc":raw_since[3],
            "eligible_events":eligible_since[0],"eligible_symbols":eligible_since[1],
            "observed_events":observed_since[0],"observed_horizons":observed_since[1],
            "eligible_unobserved_events":eligible_since[0]-observed_since[0],
            "interpretation":"raw=0 means no events saved in window; eligible=0 means none passed observation criteria; eligible>observed means pending/unresolved"
        },ensure_ascii=False),flush=True)
        cohort_rows=con.execute("""WITH ranked AS (
            SELECT o.*, ROW_NUMBER() OVER (
                PARTITION BY symbol,direction,stage,horizon_min,
                             date(event_time_utc)
                ORDER BY event_time_utc,event_id) AS rn
            FROM observational_forward o WHERE julianday(event_time_utc)>=julianday(?)
        )
        SELECT stage,horizon_min,COUNT(*),COUNT(DISTINCT symbol),
               SUM(CASE WHEN signed_return_pct>0 THEN 1 ELSE 0 END),
               SUM(CASE WHEN signed_return_pct<0 THEN 1 ELSE 0 END),
               AVG(signed_return_pct)
        FROM ranked WHERE rn=1
        GROUP BY stage,horizon_min ORDER BY stage,horizon_min""",
            (rollout_start,)).fetchall()
        cohort=[{"stage":row[0],"horizon_min":row[1],
                 "dedup_events":row[2],"unique_symbols":row[3],
                 "correct":row[4],"wrong":row[5],
                 "avg_signed_pct":row[6]} for row in cohort_rows]
        print("OBS_FORWARD_SINCE_1409_DEDUP",json.dumps({
            "start_utc":rollout_start,"start_turkey":"2026-10-10 14:09","grouping":"first symbol/direction/stage/UTC-day/horizon",
            "summary":cohort},ensure_ascii=False),flush=True)
        print("OBS_FORWARD_SUMMARY",json.dumps(result,ensure_ascii=False),flush=True)
        return result
