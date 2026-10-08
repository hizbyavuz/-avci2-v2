#!/usr/bin/env python3
"""Separate non-execution V3.2 dual-side forward-test health report."""
import argparse,json,os,sqlite3
from pathlib import Path

def summary(db):
    out={"version":"LS_V32_DUAL_SIDE_OBSERVER_2026_10_08",
         "telegram_signals":0,"orders":0,"modifies_frozen_v31":False,
         "active_symbols":0,"active_side_plans":0,
         "long_plans":0,"short_plans":0,"authorized_long":0,
         "authorized_short":0,"stages":{},"transitions":{},"issues":[]}
    if not os.path.isfile(db):
        out["issues"].append("LIVE_DB_MISSING");return out
    try:
        with sqlite3.connect("file:"+os.path.abspath(db)+"?mode=ro",uri=True) as c:
            rows=c.execute("""SELECT symbol,direction,authorized,stage
                FROM dual_side_v32 WHERE active=1""").fetchall()
            out["active_symbols"]=len(set(x[0] for x in rows))
            out["active_side_plans"]=len(rows)
            for symbol,side,authorized,stage in rows:
                k="long_plans" if side=="LONG" else "short_plans"
                out[k]+=1
                if authorized:
                    out["authorized_long" if side=="LONG" else "authorized_short"]+=1
                out["stages"][stage]=out["stages"].get(stage,0)+1
            for stage,n in c.execute("""SELECT to_stage,COUNT(*) FROM dual_side_v32_events
                GROUP BY to_stage"""):
                out["transitions"][stage]=n
    except sqlite3.Error as exc:
        out["issues"].append("DUAL_DB_INVALID:"+type(exc).__name__)
    return out

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db",default="long_short_v31_live_pool.db")
    p.add_argument("--out",default="long_short_dual_v32_health.json")
    a=p.parse_args()
    r=summary(a.db)
    Path(a.out).write_text(json.dumps(r,indent=2,ensure_ascii=False),encoding="utf-8")
    print("V32_DUAL_HEALTH",json.dumps(r,ensure_ascii=False))
if __name__=="__main__":main()
