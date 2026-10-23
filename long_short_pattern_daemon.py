"""Explicit process ownership. Never starts the retired decision engines."""
import os
from pathlib import Path
import signal
import subprocess
import threading
import time

STOP = threading.Event()
WORKERS = ('long_short_futures_universe_worker.py', 'long_short_native_derivatives.py',
           'long_short_futures_kline_worker.py', 'long_short_pattern_runtime.py')


def main():
    if os.getenv('LS_RUNTIME_PROFILE') != 'PATTERN_STRUCTURE_V1':
        raise SystemExit('EXPLICIT_PATTERN_PROFILE_REQUIRED')
    # Source/repair configuration is inherited. No new relay, region or fallback.
    workers = list(WORKERS)
    if os.getenv('LS_FUTURES_HISTORY_RELAY_URL', '').strip():
        workers.insert(-1, 'long_short_futures_history_worker.py')
    os.environ.update(LS_LEAN_RUNTIME='1', LS_NATIVE_CHAIN_V2='1', LS_CHART_FIRST_V1='1', LS_MAJOR_MOVE_V1='0')
    directory = Path(__file__).parent
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: STOP.set())
    children = {}
    try:
        while not STOP.is_set():
            for script in workers:
                child = children.get(script)
                if child is None:
                    children[script] = subprocess.Popen(['python', '-u', str(directory/script)], cwd=directory)
                elif child.poll() is not None:
                    # Fail the whole service so broken required workers cannot
                    # coexist with a falsely healthy supervisor indefinitely.
                    raise RuntimeError('REQUIRED_WORKER_EXITED:'+script)
            STOP.wait(2)
    finally:
        for child in children.values():
            if child.poll() is None: child.terminate()
        deadline = time.monotonic()+10
        for child in children.values():
            try: child.wait(timeout=max(.1, deadline-time.monotonic()))
            except subprocess.TimeoutExpired: child.kill(); child.wait()


if __name__ == '__main__':
    main()
