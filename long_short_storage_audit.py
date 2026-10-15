#!/usr/bin/env python3
"""Read-only persistent-volume capacity and SQLite table footprint audit.
Never deletes, vacuums, or modifies live databases.
"""
import json
import os
import shutil
import sqlite3
from pathlib import Path

def inspect(root, deep=True):
    usage=shutil.disk_usage(root)
    out={"volume":str(root),"disk_total_bytes":usage.total,
         "disk_used_bytes":usage.used,"disk_free_bytes":usage.free,
         "disk_used_pct":round(100*usage.used/usage.total,2) if usage.total else None,
         "files":[]}
    archive=root/'legacy-archive'
    if archive.is_dir():
        out['legacy_archive_bytes']=sum(p.stat().st_size for p in archive.iterdir() if p.is_file())
    for path in sorted(root.iterdir()):
        if not path.is_file():
            continue
        try:
            size=path.stat().st_size
        except OSError:
            continue
        if size<1024*1024 and not path.name.endswith(("-wal","-shm")):
            continue
        row={"name":path.name,"bytes":size}
        if path.suffix==".db":
            try:
                with sqlite3.connect(f"file:{path}?mode=ro",uri=True,timeout=2) as db:
                    page_size=db.execute("PRAGMA page_size").fetchone()[0]
                    pages=db.execute("PRAGMA page_count").fetchone()[0]
                    free=db.execute("PRAGMA freelist_count").fetchone()[0]
                    row["sqlite_free_bytes"]=free*page_size
                    row["sqlite_pages_bytes"]=pages*page_size
                    if deep:
                        try:
                            row["largest_tables"]=[{"table":name,"bytes":amount} for name,amount in
                                db.execute("SELECT name,sum(pgsize) FROM dbstat GROUP BY name ORDER BY sum(pgsize) DESC LIMIT 8")]
                        except sqlite3.Error:
                            row["largest_tables"]="dbstat_unavailable"
            except (sqlite3.Error,OSError) as exc:
                row["inspection_error"]=type(exc).__name__
        out["files"].append(row)
    out["files"].sort(key=lambda x:x["bytes"],reverse=True)
    return out

if __name__=="__main__":
    root=Path(os.getenv("LS_STATE_DIR") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or ".long-short-state")
    if not root.is_dir():
        raise SystemExit("state directory missing")
    print("LS_STORAGE_AUDIT "+json.dumps(inspect(root,deep=os.getenv('LS_STORAGE_DEEP_AUDIT','1')=='1'),separators=(",",":")),flush=True)
