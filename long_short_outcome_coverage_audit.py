#!/usr/bin/env python3
"""Read-only, fail-closed outcome coverage audit. Never promotes watches to trades."""
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.getenv("LS_STATE_DIR") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or ".long-short-state")
DBS = {
    "analyst": "long_short_analyst.db",
    "live": "long_short_live_pool.db",
    "research": "long_short_research.db",
    "early": "early_forward_outcomes.sqlite",
}

def inspect_db(filename):
    path = ROOT / filename
    if not path.is_file():
        return {"available": False, "reason": "database_missing"}
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)
        try:
            if con.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                return {"available": False, "reason": "integrity_check_failed"}
            names = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            result = {"available": True, "tables": {}}
            for name in ("paper_setups", "performance_reports", "confirmed_trade_outcomes",
                         "signal_forward", "observational_forward", "primary_events",
                         "research_reports", "watch_episodes", "watch_451_symbol_notices"):
                if name in names:
                    result["tables"][name] = con.execute('SELECT COUNT(*) FROM "' + name + '"').fetchone()[0]
            if "primary_events" in names:
                cols = {r[1] for r in con.execute("PRAGMA table_info(primary_events)")}
                if "data_cohort" in cols:
                    result["native_primary_events"] = con.execute(
                        "SELECT COUNT(*) FROM primary_events WHERE data_cohort=?",
                        ("BINANCE_FUTURES_NATIVE",)).fetchone()[0]
            if "events" in names:
                cols = {r[1] for r in con.execute("PRAGMA table_info(events)")}
                if "stage_to" in cols:
                    result["triggered_events"] = con.execute(
                        "SELECT COUNT(*) FROM events WHERE stage_to='TRIGGERED'").fetchone()[0]
                    if {"telegram_status", "telegram_sent_time_utc", "price"} <= cols:
                        result["delivered_trigger_events"] = con.execute(
                            "SELECT COUNT(*) FROM events WHERE stage_to='TRIGGERED' "
                            "AND telegram_status='SENT' AND telegram_sent_time_utc IS NOT NULL "
                            "AND price>0").fetchone()[0]
            return result
        finally:
            con.close()
    except (OSError, sqlite3.Error) as exc:
        return {"available": False, "reason": type(exc).__name__}

def main():
    db = {name: inspect_db(filename) for name, filename in DBS.items()}
    live = db["live"]
    analyst = db["analyst"]
    research = db["research"]
    actual = live.get("tables", {}).get("confirmed_trade_outcomes", 0)
    native = research.get("native_primary_events", 0)
    report = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "read_only_coverage_not_profitability",
        "databases": db,
        "confirmed_trade_outcomes": actual,
        "native_primary_events": native,
        "observational_records": live.get("tables", {}).get("observational_forward"),
        "paper_setups": analyst.get("tables", {}).get("paper_setups"),
        "can_report_trade_win_rate": bool(actual and native and all(x.get("available") for x in (live, research))),
        "warnings": [],
    }
    if not all(x.get("available") for x in (live, research)):
        report["warnings"].append("CRITICAL_DATABASE_UNAVAILABLE")
    if not native:
        report["warnings"].append("NO_NATIVE_BINANCE_FUTURES_PRIMARY_EVENTS")
    if not actual:
        report["warnings"].append("NO_CONFIRMED_TRADE_OUTCOMES")
    if report["observational_records"]:
        report["warnings"].append("OBSERVATIONAL_FORWARD_IS_NOT_TRADE_WIN_RATE")
    print("LS_OUTCOME_COVERAGE_AUDIT " + json.dumps(report, ensure_ascii=False, sort_keys=True), flush=True)

if __name__ == "__main__":
    main()
