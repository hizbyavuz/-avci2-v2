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
    con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
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
    stages, unique, reasons = Counter(), {}, Counter()
    for row in rows:
        stage = str(row["stage_to"])
        stages[stage] += 1
        unique.setdefault(stage,set()).add(str(row["symbol"]))
        try:
            p = json.loads(row["payload_json"] or "{}")
        except (ValueError,TypeError):
            p = {}
        if stage == "EXECUTION_BLOCKED":
            reasons[str((p.get("_trigger_execution_gate") or {}).get("reason","UNKNOWN"))] += 1
    return {"events":len(rows),"stage_transitions":dict(stages),
            "unique_symbols_per_stage":{k:len(v) for k,v in unique.items()},
            "execution_block_reasons":dict(reasons)}

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
        a=scan(analyst,"analyses","scan_time_utc",args.since)
        report["analyst"] = summarize_analyses(a) if isinstance(a,list) else a
        v=scan(live,"events","event_time_utc",args.since)
        report["live"] = summarize_live(v) if isinstance(v,list) else v
    finally:
        for con in (analyst,live):
            if con: con.close()
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=="__main__":
    main()
