#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Write-only New Launch outcome/runner observer.

No classification, score, threshold, security veto or Telegram decision is changed.
"""
import os, sqlite3, json
DB=os.getenv("NEW_LAUNCH_DB",".new-launch-state/new_launch_avci.db")
VERSION="new-launch-audit-v1-20260930"
LEVELS=(20,30,50,100,200)

def table(c,t):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None

def main():
    if not os.path.exists(DB):
        print(json.dumps({"status":"MISSING_DB","db":DB})); return
    with sqlite3.connect(DB,timeout=30) as c:
        c.row_factory=sqlite3.Row
        c.executescript("""
        CREATE TABLE IF NOT EXISTS audit_new_launch_signal_summary(
          network TEXT,contract TEXT,signal_ts INTEGER,first_seen_ts INTEGER,detection_delay_sec REAL,
          ruleset_version TEXT,max_return_pct REAL,min_return_pct REAL,uncapped_mfe_pct REAL,
          hit_20 INTEGER,hit_30 INTEGER,hit_50 INTEGER,hit_100 INTEGER,hit_200 INTEGER,
          terminal_state TEXT,version TEXT,PRIMARY KEY(network,contract,signal_ts,version));
        CREATE TABLE IF NOT EXISTS audit_new_launch_scan_integrity(
          run_id TEXT,scan_ts INTEGER,network TEXT,contract TEXT,created_at TEXT,age_min REAL,
          classification TEXT,security_label TEXT,liquidity REAL,retention_pct REAL,
          version TEXT,PRIMARY KEY(run_id,network,contract,version));
        """)
        n=0
        if table(c,"signals"):
            sigs=c.execute("SELECT network,contract,first_signal_ts FROM signals").fetchall()
            for s in sigs:
                rows=c.execute(
                    "SELECT checked_ts,return_pct,mfe_pct,mae_pct FROM signal_outcomes "
                    "WHERE network=? AND contract=? AND signal_ts=? ORDER BY checked_ts",
                    (s["network"],s["contract"],s["first_signal_ts"])
                ).fetchall() if table(c,"signal_outcomes") else []
                rets=[float(r["return_pct"]) for r in rows if r["return_pct"] is not None]
                mfes=[float(r["mfe_pct"]) for r in rows if r["mfe_pct"] is not None]
                first=c.execute(
                    "SELECT MIN(scan_ts) FROM observations WHERE network=? AND contract=?",
                    (s["network"],s["contract"])
                ).fetchone()[0] if table(c,"observations") else None
                mfe=max(mfes) if mfes else (max(rets) if rets else None)
                vals=[int(mfe is not None and mfe>=x) for x in LEVELS]
                terminal="OBSERVED" if rows else "PENDING"
                delay=(float(first)-float(s["first_signal_ts"])) if first is not None else None
                c.execute(
                    "INSERT OR REPLACE INTO audit_new_launch_signal_summary VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (s["network"],s["contract"],s["first_signal_ts"],first,delay,
                     "new-launch-current-20260930",
                     max(rets) if rets else None,min(rets) if rets else None,mfe,
                     *vals,terminal,VERSION)
                )
                n+=1
        if table(c,"observations"):
            c.execute("DELETE FROM audit_new_launch_scan_integrity WHERE version=?",(VERSION,))
            c.execute("""INSERT INTO audit_new_launch_scan_integrity
              SELECT run_id,scan_ts,network,contract,created_at,age_min,classification,security_label,
                     liquidity,retention_pct,?
              FROM observations""",(VERSION,))
        c.commit()
        print(json.dumps({"status":"OK","signals_summarized":n,"version":VERSION}))

if __name__=="__main__":
    main()
