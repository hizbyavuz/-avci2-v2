#!/usr/bin/env python3
"""Compare frozen V3.1 vs research-only V3.2 distinct-barrier room on same scan."""
import argparse
import json
import os
import sqlite3
from collections import Counter
from pathlib import Path

def report(db):
    out={"model":"LS_V3_2_DISTINCT_ROOM_SHADOW_2026_10_08",
         "changes_frozen_v31":False,"telegram_trades":False,
         "scan_time_utc":None,"counts":{},"examples":[],"issues":[]}
    if not os.path.isfile(db):
        out["issues"].append("NO_ANALYST_DATABASE")
        return out
    with sqlite3.connect("file:"+os.path.abspath(db)+"?mode=ro",uri=True) as conn:
        t=conn.execute("SELECT MAX(scan_time_utc) FROM analyses").fetchone()[0]
        if t is None:
            out["issues"].append("NO_ANALYSES")
            return out
        out["scan_time_utc"]=t
        rows=conn.execute("SELECT symbol,status,payload_json FROM analyses WHERE scan_time_utc=?",(t,)).fetchall()
    c=Counter()
    c["deep_scanned"]=len(rows)
    for symbol,status,serialized in rows:
        try:p=json.loads(serialized or "{}")
        except (TypeError,ValueError):c["malformed_analyses"]+=1;continue
        old=p.get("structure_gate") or {}
        new=p.get("v32_distinct_room_shadow") or {}
        if bool(old.get("room_ok")):c["frozen_v31_room_pass"]+=1
        if bool(old.get("qualified_precheck")):c["frozen_v31_precheck_pass"]+=1
        if not new:
            c["shadow_not_in_snapshot"]+=1
            continue
        if not new.get("valid"):
            c["shadow_invalid"]+=1
            continue
        c["shadow_valid"]+=1
        if new.get("room_ok"):c["v32_distinct_room_pass"]+=1
        if new.get("fully_qualified_geometry"):c["v32_room_and_r_pass"]+=1
        if not old.get("room_ok") and new.get("fully_qualified_geometry"):
            c["old_room_fail_new_room_and_r_pass"]+=1
        if (old.get("next_opposing_zone") and old.get("trigger_zone")):
            from long_short_room_v32_shadow import overlaps
            if overlaps(old["next_opposing_zone"],old["trigger_zone"]):
                c["frozen_next_zone_overlapped_trigger"]+=1
        if len(out["examples"])<40:
            out["examples"].append({"symbol":symbol,"status":status,
                "frozen_room_pct":old.get("room_pct"),
                "frozen_room_ok":old.get("room_ok"),
                "new_room_pct":new.get("room_pct"),
                "new_room_ok":new.get("room_ok"),
                "new_net_r":new.get("net_t1_r"),
                "new_valid":new.get("valid"),
                "overlapping_zones_ignored":new.get("overlapping_zones_ignored"),
                "zone_clearance_pct":new.get("entry_clearance_pct")})
    out["counts"]=dict(sorted(c.items()))
    return out

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db",default="long_short_v31_analyst.db")
    p.add_argument("--out",default="long_short_v32_room_shadow_report.json")
    a=p.parse_args()
    result=report(a.db)
    Path(a.out).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print("V32_ROOM_SHADOW",json.dumps(result["counts"],sort_keys=True),"issues",result["issues"])
    print("FROZEN_V31_SIGNAL_RULES_UNCHANGED")

if __name__=="__main__":main()
