#!/usr/bin/env python3
"""Read-only audit: detect source references and live process handles before DB cleanup."""
import json
import os
from pathlib import Path

ROOT=Path(__file__).resolve().parent
STATE=Path(os.getenv("LS_STATE_DIR") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or ".long-short-state")
TARGET="long_short_v4_native.db"

def main():
    refs=[]
    for path in ROOT.glob("*.py"):
        try:
            for number,line in enumerate(path.read_text(encoding="utf-8").splitlines(),1):
                if TARGET in line or "long_short_v4_native_pipeline" in line:
                    refs.append({"file":path.name,"line":number,"snippet":line.strip()[:180]})
        except OSError:
            continue
    handles=[]
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        fd=proc/"fd"
        try:
            for entry in fd.iterdir():
                try:
                    link=os.readlink(entry)
                    if TARGET in link:
                        handles.append({"pid":int(proc.name),"fd":entry.name,"path":link[:240]})
                except OSError:
                    pass
        except (OSError,PermissionError):
            continue
    target=STATE/TARGET
    print("LS_NATIVE_DB_DEPENDENCY_AUDIT "+json.dumps({
        "target":str(target),"exists":target.exists(),
        "size_bytes":target.stat().st_size if target.exists() else 0,
        "source_references":refs,"open_handles":handles,
        "safe_to_delete":False,
        "note":"Diagnostic only; no files modified."},separators=(",",":")),flush=True)

if __name__=="__main__":
    main()
