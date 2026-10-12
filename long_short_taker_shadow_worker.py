"""Dedicated read-only Binance Futures taker shadow worker.

Rotates shortlist from the existing analyst database, retries connections with
bounded backoff, and never changes production signals or historical DBs.
"""
import json
import os
import signal
import threading
import time
from pathlib import Path
from long_short_v4_native_pipeline import choose_symbols
from long_short_taker_continuous_shadow import collect

STOP=threading.Event()

def shutdown(*_):
    STOP.set()

def main():
    signal.signal(signal.SIGTERM,shutdown)
    signal.signal(signal.SIGINT,shutdown)
    root=Path(os.getenv("LS_STATE_DIR") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or ".long-short-state")
    root.mkdir(parents=True,exist_ok=True)
    db=str(root/"long_short_taker_minute_shadow.db")
    analyst=os.getenv("LS_ANALYST_DB") or str(root/"long_short_analyst.db")
    backoff=5
    while not STOP.is_set():
        symbols=choose_symbols("",analyst_db=analyst,limit=24)
        result=collect(db,symbols,STOP,rotation_seconds=900)
        print("BINANCE_TAKER_SHADOW "+json.dumps(result),flush=True)
        if result.get("state")=="ROTATE":
            backoff=5
            continue
        if STOP.is_set():
            break
        STOP.wait(backoff)
        backoff=min(60,backoff*2)
    return 0

if __name__=="__main__":
    raise SystemExit(main())
