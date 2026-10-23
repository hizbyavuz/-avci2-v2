"""Existing live motor plus an isolated, non-notifying TA measurement worker.

Selecting this entrypoint explicitly enables the shadow worker. A shadow error
does not stop/restart the existing motor; no new collector or order API is added.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from structure_engine.config import CONFIG
from long_short_runtime_resources import snapshot


def process_resources(children):
    """Include existing collector grandchildren in the unchanged RSS budget."""
    found={};pending=[child.pid for child in children if child is not None and child.poll() is None]
    while pending:
        pid=pending.pop()
        if pid in found:continue
        found[pid]=dict(state='RUNNING',pid=pid)
        try:
            pending.extend(int(v) for v in Path(f'/proc/{pid}/task/{pid}/children').read_text().split())
        except (OSError,ValueError):pass
    return snapshot({str(pid):value for pid,value in found.items()})


def supervise(root, stop, spawn=subprocess.Popen):
    root=Path(root).resolve()
    env=dict(os.environ,LS_STATE_DIR=str(root))
    existing=spawn([sys.executable,'-u','structure_live_start.py'],env=env)
    shadow=None; attempted=False; reported=False;last_metrics=0.
    source=root/'binance_futures_kline_shadow.db'; metadata=root/'native_derivatives_v2.db'
    try:
        while not stop.is_set():
            code=existing.poll()
            if code is not None:
                print('STRUCTURE_PRIMARY_EXIT '+str(code),flush=True)
                return code if code else 1
            if not attempted and source.is_file() and metadata.is_file():
                attempted=True
                try:
                    shadow=spawn([sys.executable,'-u','-m','technical_observer_v1.shadow_runtime',
                        '--source-db',str(source),'--metadata-db',str(metadata),
                        '--state-dir',str(root/'technical_observer_shadow_v1')],
                        env=dict(env,TA_OBSERVER_SHADOW='1'))
                    print('TA_SHADOW_STARTED '+json.dumps(dict(pid=shadow.pid,notifications=False,
                          orders=False,additional_collectors=0)),flush=True)
                except OSError as exc:
                    print('TA_SHADOW_START_FAILED '+type(exc).__name__+' PRIMARY_CONTINUES',flush=True)
            metrics=process_resources((existing,shadow))
            if shadow and shadow.poll() is None and metrics['total_process_rss_mb']>CONFIG['memory_rss_limit_mb']:
                print('TA_SHADOW_COMBINED_RSS_GUARD PRIMARY_CONTINUES',flush=True)
                shadow.terminate()
                try:shadow.wait(timeout=10)
                except subprocess.TimeoutExpired:shadow.kill();shadow.wait(timeout=5)
            if time.monotonic()-last_metrics>=30:
                last_metrics=time.monotonic()
                print('TA_SHADOW_RESOURCES '+json.dumps(metrics,sort_keys=True),flush=True)
            if shadow and shadow.poll() is not None and not reported:
                reported=True
                print('TA_SHADOW_EXIT '+str(shadow.returncode)+' PRIMARY_CONTINUES_NO_AUTORESTART',flush=True)
            stop.wait(2)
    finally:
        for child in (shadow,existing):
            if child is not None and child.poll() is None:
                child.terminate()
        for child in (shadow,existing):
            if child is not None:
                try: child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    child.kill();child.wait(timeout=5)
    return 0


def main():
    if os.getenv('LS_RUNTIME_PROFILE')!='STRUCTURE_CONTEXT_V1' or os.getenv('STRUCTURE_NOTIFICATIONS')!='1':
        raise SystemExit('EXPLICIT_EXISTING_LIVE_OPT_IN_REQUIRED')
    stop=threading.Event()
    for sig in (signal.SIGINT,signal.SIGTERM): signal.signal(sig,lambda *_:stop.set())
    root=Path(os.getenv('LS_STATE_DIR') or os.getenv('RAILWAY_VOLUME_MOUNT_PATH') or '.long-short-state')
    return supervise(root,stop)


if __name__=='__main__': raise SystemExit(main())
