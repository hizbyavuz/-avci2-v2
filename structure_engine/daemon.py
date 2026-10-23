"""Opt-in supervisor for shared collectors plus ONLY the new decision runtime.

Not launched by the default Dockerfile or any existing Railway deployment.
"""
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from .config import CONFIG as C
from .store import encoded, resource_check


def main():
    if os.getenv('LS_RUNTIME_PROFILE') != 'STRUCTURE_CONTEXT_V1':
        raise SystemExit('EXPLICIT_STRUCTURE_PROFILE_REQUIRED')
    base = Path(__file__).resolve().parents[1]
    root = Path(os.getenv('LS_STATE_DIR') or os.getenv('RAILWAY_VOLUME_MOUNT_PATH') or base/'.long-short-state')
    interval = os.getenv('STRUCTURE_SETUP_INTERVAL', '15m')
    if interval not in ('15m', '30m'): raise SystemExit('INVALID_SETUP_INTERVAL')
    # Reuse the already tested full-universe collector demand routing. This
    # child-only profile never launches the old decision runtime or notifier.
    collector_env = dict(os.environ, LS_RUNTIME_PROFILE='PATTERN_STRUCTURE_V2', LS_STATE_DIR=str(root),
                         PATTERN_NOTIFICATIONS='0', SWING_NOTIFICATIONS='0', LS_MAJOR_MOVE_V1='0',
                         LS_LEAN_RUNTIME='1', LS_NATIVE_CHAIN_V2='1', LS_CHART_FIRST_V1='1')
    scripts = ['long_short_futures_universe_worker.py', 'long_short_native_derivatives.py',
               'long_short_futures_kline_worker.py']
    if os.getenv('LS_FUTURES_HISTORY_RELAY_URL', '').strip():
        scripts.append('long_short_futures_history_worker.py')
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM): signal.signal(sig, lambda *_: stop.set())
    children = {}
    try:
        limits = resource_check(root)
        if not limits['ok']: raise RuntimeError('RESOURCE_GUARD:'+','.join(limits['reasons']))
        for script in scripts:
            children[script] = subprocess.Popen([sys.executable, '-u', str(base/script)], cwd=base, env=collector_env)
        source = root/'binance_futures_kline_shadow.db'
        while not stop.is_set():
            if 'decision_runtime' not in children and source.is_file():
                command = [sys.executable, '-u', '-m', 'structure_engine.runtime', '--source-db', str(source),
                           '--state-dir', str(root/'structure_context_v1_3'), '--interval', interval]
                if os.getenv('STRUCTURE_NOTIFICATIONS') == '1': command.append('--notify')
                children['decision_runtime'] = subprocess.Popen(command, cwd=base, env=dict(os.environ, LS_STATE_DIR=str(root)))
            for name, child in children.items():
                if child.poll() is not None: raise RuntimeError('REQUIRED_WORKER_EXITED:'+name)
            from long_short_runtime_resources import snapshot
            metrics = snapshot({name: {'state':'RUNNING','pid':child.pid} for name, child in children.items()})
            limits = resource_check(root)
            print('STRUCTURE_RESOURCES '+encoded(dict(processes=metrics, disk=limits)), flush=True)
            if not limits['ok'] or metrics['total_process_rss_mb'] > C['memory_rss_limit_mb']:
                raise RuntimeError('STRUCTURE_RESOURCE_GUARD_STOPPED_ALL_CHILDREN')
            stop.wait(30)
    finally:
        for child in children.values():
            if child.poll() is None: child.terminate()
        deadline = time.monotonic()+10
        for child in children.values():
            try: child.wait(timeout=max(.1, deadline-time.monotonic()))
            except subprocess.TimeoutExpired: child.kill(); child.wait()


if __name__ == '__main__':
    main()
