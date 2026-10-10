#!/usr/bin/env python3
"""Read-only LONG/SHORT funnel audit. Does not import production modules."""
import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

def connect(path):
    if not path.is_file():
        return None
    con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    con.execute("PRAGMA query_only=ON")
    con.row_factory = sqlite3.Row
    return con

def table_exists(con, table):
    return con is not None and con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None

def columns(con, table):
    return {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}

def scan(con, table, time_column, since):
    if not table_exists(con, table):
        return {"status": "missing_table"}
    if time_column not in columns(con, table):
        return {"status": "missing_time_column"}
    rows = con.execute(
        f"SELECT * FROM {table} WHERE julianday({time_column}) >= julianday(?)",
        (since,)
    )
    return list(rows)

def summarize_analyses(rows):
    statuses, reasons, admission, per_scan = Counter(), Counter(), Counter(), Counter()
    for row in rows:
        statuses[str(row["status"])] += 1
        per_scan[str(row["scan_time_utc"])] += 1
        try:
            p = json.loads(row["payload_json"] or "{}")
        except (ValueError, TypeError):
            p = {}
        plan = p.get("setup_plan") or {}
        gate = p.get("structure_gate") or {}
        v3 = p.get("v3") or {}
        if not plan.get("direction") or plan.get("trigger_level") is None:
            admission["NO_PLAN"] += 1
        elif row["status"] == "NO_TRADE" and abs(float(p.get("day_change_pct") or 0)) >= 5:
            admission["RADAR_CANDIDATE"] += 1
        elif row["status"] not in ("WAIT", "LONG", "SHORT"):
            admission["NOT_ACTIONABLE_STATUS"] += 1
        else:
            admission["POTENTIAL_ACTIONABLE_NOT_VERIFIED"] += 1
        if not p.get("derivatives_ready"):
            reasons["DERIVATIVES_NOT_READY"] += 1
        if "v3" in p and not v3.get("eligible"):
            reasons["V3_NOT_ELIGIBLE"] += 1
        if not gate.get("qualified_precheck"):
            reasons["PRECHECK_NOT_QUALIFIED"] += 1
    return {"rows":len(rows), "statuses":dict(statuses),
            "diagnostic_flags_not_exclusive":dict(reasons),
            "rough_categories_not_production_admission":dict(admission),
            "scans":len(per_scan), "rows_per_scan":dict(per_scan)}

def summarize_live(rows):
    stages, unique, reasons, delivery = Counter(), {}, Counter(), Counter()
    for row in rows:
        stage = str(row["stage_to"])
        delivery[(stage,str(row["telegram_status"]))] += 1
        stages[stage] += 1
        unique.setdefault(stage,set()).add(str(row["symbol"]))
        try:
            p = json.loads(row["payload_json"] or "{}")
        except (ValueError,TypeError):
            p = {}
        if stage == "EXECUTION_BLOCKED":
            reasons[str((p.get("_trigger_execution_gate") or {}).get("reason") or p.get("_execution_block_reason") or "UNKNOWN")] += 1
    return {"events":len(rows),"stage_transitions":dict(stages),
            "unique_symbols_per_stage":{k:len(v) for k,v in unique.items()},
            "execution_block_reasons":dict(reasons),
            "telegram_status_by_stage":{"/".join(k):v for k,v in delivery.items()}}

def summarize_episodes(rows):
    return {"episodes":len(rows),"end_reasons":dict(Counter(str(r["end_reason"] or "OPEN") for r in rows)),
            "max_stages":dict(Counter(str(r["max_stage"]) for r in rows))}

def summarize_outcomes(rows):
    return {"outcomes":len(rows),"results":dict(Counter(str(r["result"]) for r in rows))}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--state-dir",default="/app/.long-short-state")
    parser.add_argument("--since",required=True)
    parser.add_argument("--out",default="/tmp/ls_funnel_audit.json")
    args=parser.parse_args()
    state=Path(args.state_dir).resolve()
    out=Path(args.out).resolve()
    if out==state or state in out.parents:
        parser.error("Output must not be inside state volume")
    try:
        datetime.fromisoformat(args.since.replace("Z","+00:00"))
    except ValueError:
        parser.error("--since must be ISO datetime")
    analyst=connect(state/"long_short_analyst.db")
    live=connect(state/"long_short_live_pool.db")
    report={"generated_utc":datetime.now(timezone.utc).isoformat(),
            "since":args.since,"read_only":True,
            "note":"Diagnostic counts only; not validated production admission or trade performance"}
    try:
        for label,con,table,time_col,fn in (("analyst",analyst,"analyses","scan_time_utc",summarize_analyses),("live",live,"events","event_time_utc",summarize_live),("episodes",live,"watch_episodes","started_at_utc",summarize_episodes),("confirmed_outcomes",live,"confirmed_trade_outcomes","entry_time_utc",summarize_outcomes)):
            try:
                rows=scan(con,table,time_col,args.since)
                report[label]=fn(rows) if isinstance(rows,list) else rows
            except (sqlite3.Error,KeyError,TypeError,ValueError) as exc:
                report[label]={"status":"error","error":type(exc).__name__,"detail":str(exc)[:200]}
    finally:
        for con in (analyst,live):
            if con: con.close()
    if out.parent != Path("/tmp"):
        parser.error("Output must be directly under /tmp")
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=="__main__":
    main()
