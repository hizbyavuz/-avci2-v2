#!/usr/bin/env python3
"""Read-only operational audit for Long/Short. Does not change trading rules."""
import hashlib, json, os, sqlite3, subprocess
from datetime import datetime, timezone
from pathlib import Path

STATE=Path(os.getenv("LS_STATE_DIR") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or ".long-short-state")
PRIMARY="BINANCE_FUTURES_NATIVE"

def git_sha():
    for key in ("RAILWAY_GIT_COMMIT_SHA","GITHUB_SHA","LS_GIT_COMMIT_SHA"):
        if os.getenv(key): return os.environ[key]
    try: return subprocess.check_output(["git","rev-parse","HEAD"],text=True,stderr=subprocess.DEVNULL,timeout=2).strip()
    except Exception: return "UNAVAILABLE"

def sha(path):
    p=Path(path)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None

def audit_db(path):
    out={"file":path.name,"bytes":path.stat().st_size,"integrity":None,"tables":{}}
    try:
        con=sqlite3.connect(f"file:{path}?mode=ro",uri=True,timeout=3)
        out["integrity"]=con.execute("PRAGMA quick_check").fetchone()[0]
        names=[r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for name in names:
            if not name.replace("_","").isalnum(): continue
            try: out["tables"][name]=con.execute('SELECT COUNT(*) FROM "'+name+'"').fetchone()[0]
            except sqlite3.Error: out["tables"][name]=None
        if "primary_events" in names:
            cols=[r[1] for r in con.execute("PRAGMA table_info(primary_events)")]
            if "data_cohort" in cols:
                out["cohorts"]=dict(con.execute("SELECT data_cohort,COUNT(*) FROM primary_events GROUP BY data_cohort").fetchall())
                out["primary_count"]=out["cohorts"].get(PRIMARY,0)
                out["independent_episodes"]=con.execute("SELECT COUNT(DISTINCT episode_id) FROM primary_events WHERE data_cohort=?", (PRIMARY,)).fetchone()[0]
        if "events" in names:
            cols=[r[1] for r in con.execute("PRAGMA table_info(events)")]
            if "stage_to" in cols:
                out["triggered_count"]=con.execute("SELECT COUNT(*) FROM events WHERE stage_to='TRIGGERED'").fetchone()[0]
                if "telegram_sent_time_utc" in cols:
                    out["triggered_delivered"]=con.execute("SELECT COUNT(*) FROM events WHERE stage_to='TRIGGERED' AND telegram_sent_time_utc IS NOT NULL").fetchone()[0]
        if "paper_events" in names:
            cols=[r[1] for r in con.execute("PRAGMA table_info(paper_events)")]
            if "data_cohort" in cols:
                out["cohorts"]=dict(con.execute("SELECT data_cohort,COUNT(*) FROM paper_events GROUP BY data_cohort").fetchall())
                out["primary_count"]=out["cohorts"].get(PRIMARY,0)
        con.close()
    except Exception as exc: out["error"]=str(exc)
    return out

def main():
    result={"utc":datetime.now(timezone.utc).isoformat(),"git_commit":git_sha(),"state_dir":str(STATE),"config_hashes":{},"databases":[]}
    for name in ("LONG_SHORT_V3_FROZEN_CONFIG.json","LONG_SHORT_V1_9_FROZEN_CONFIG.json"):
        result["config_hashes"][name]=sha(name)
    if STATE.is_dir():
        result["databases"]=[audit_db(p) for p in sorted(STATE.glob("*.db"))]
    else: result["error"]="STATE_DIRECTORY_MISSING"
    print("LS_AUDIT "+json.dumps(result,ensure_ascii=False,sort_keys=True))
    return 0 if all(x.get("integrity")=="ok" for x in result["databases"]) else 2

if __name__=="__main__": raise SystemExit(main())
