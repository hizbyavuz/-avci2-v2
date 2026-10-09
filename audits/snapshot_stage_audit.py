"""Read-only stage coverage audit for a user-prepared SQLite snapshot.

Usage: python audits/snapshot_stage_audit.py /path/to/snapshot.sqlite
Never connects to Railway; never modifies input DB. Python stdlib only.
"""
import argparse
import json
import sqlite3
from collections import Counter

def has_table(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None

def cols(db, name):
    return {r[1] for r in db.execute(f"PRAGMA table_info({name})")}

def main():
    p = argparse.ArgumentParser()
    p.add_argument("snapshot", help="Offline sanitized SQLite snapshot, NOT live volume")
    a = p.parse_args()
    uri = "file:" + __import__("pathlib").Path(a.snapshot).resolve().as_posix() + "?mode=ro&immutable=1"
    db = sqlite3.connect(uri, uri=True)
    names = ["universe_observations", "opportunity_funnel", "scans", "live_pool_events", "telegram_delivery_ledger"]
    report = {"read_only": True, "tables": {}, "limitations": []}
    for name in names:
        if not has_table(db, name):
            report["tables"][name] = {"exists": False}
            continue
        n = db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        report["tables"][name] = {"exists": True, "rows": n, "columns": sorted(cols(db, name))}
    if has_table(db, "opportunity_funnel"):
        report["funnel_stages"] = dict(db.execute(
            "SELECT stage, COUNT(*) FROM opportunity_funnel GROUP BY stage").fetchall())
        required = {"scan_time_utc", "symbol", "stage", "detail_json"}
        if required.issubset(cols(db, "opportunity_funnel")):
            counts = Counter()
            samples = Counter()
            for ts, sym, stage, raw in db.execute(
                "SELECT scan_time_utc,symbol,stage,detail_json FROM opportunity_funnel"):
                try:
                    d = json.loads(raw or "{}")
                    dec = d.get("production_direction") or {}
                    reasons = dec.get("hard_blockers") or dec.get("blockers") or []
                    if isinstance(reasons, str):
                        reasons = [reasons]
                    for reason in reasons:
                        counts[str(reason)] += 1
                    if stage == "MODEL_NO_TRADE" and not reasons:
                        samples["no_trade_without_explicit_blocker"] += 1
                except (ValueError, TypeError):
                    samples["malformed_detail_json"] += 1
            report["explicit_blockers"] = dict(counts)
            report["diagnostics"] = dict(samples)
    report["limitations"] += [
        "Not an independent missed-mover test: full historical perp 1m candles are required.",
        "A preselected-only funnel cannot prove coverage of all perp symbols.",
        "Without time-matched Telegram/live event tables, sent/triggered stages are UNMEASURABLE.",
        "Do not treat absent rows as rejected coins or missing outcomes as losses.",
    ]
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    db.close()

if __name__ == "__main__":
    main()
