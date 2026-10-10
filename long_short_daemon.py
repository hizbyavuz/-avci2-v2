#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""24/7 supervisor for the Long/Short motor.

Designed for persistent container hosts such as Railway.
GitHub remains the code source; GitHub Actions is not required for runtime.

Processes:
- analyst: refreshed on each 5-minute UTC boundary
- continuation live pool: continuously restarted after its aligned watch window
- reversal live pool: continuously restarted after its aligned watch window
- research: V2 shadow + frozen validation periodically

All SQLite state is redirected to a persistent volume when available.
Never places orders.
"""
from __future__ import annotations

import json
import math
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STOP = threading.Event()
STATUS_LOCK = threading.Lock()
STATUS: dict[str, dict] = {}

ANALYST_INTERVAL = int(os.getenv("LS_DAEMON_ANALYST_INTERVAL_SECONDS", "300"))
RESEARCH_INTERVAL = int(os.getenv("LS_DAEMON_RESEARCH_INTERVAL_SECONDS", "3600"))
RESEARCH_INITIAL_DELAY = int(os.getenv("LS_DAEMON_RESEARCH_INITIAL_DELAY_SECONDS", "600"))
RESTART_BACKOFF = float(os.getenv("LS_DAEMON_RESTART_BACKOFF_SECONDS", "5"))
BOUNDARY_GRACE = float(os.getenv("LS_DAEMON_BOUNDARY_GRACE_SECONDS", "6"))
HEARTBEAT_SECONDS = int(os.getenv("LS_DAEMON_HEARTBEAT_SECONDS", "60"))
BACKUP_INTERVAL = int(os.getenv("LS_BACKUP_INTERVAL_SECONDS", "86400"))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def state_dir() -> Path:
    configured = (os.getenv("LS_STATE_DIR") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or "").strip()
    if configured:
        p = Path(configured)
    elif Path("/data").exists():
        p = Path("/data")
    else:
        p = ROOT / ".long-short-state"
    p.mkdir(parents=True, exist_ok=True)
    return p


def configure_state_paths() -> Path:
    sd = state_dir()
    paths = {
        "LS_DB": sd / "long_short_analyst.db",
        "LS_ANALYST_DB": sd / "long_short_analyst.db",
        "LS_LIVE_DB": sd / "long_short_live_pool.db",
        "LS_REVERSAL_DB": sd / "long_short_reversal_live.db",
        "LS_SIMPLE_NOTIFY_DB": sd / "long_short_simple_notify.db",
        "LS_RESEARCH_DB": sd / "long_short_research.db",
        "LS_RESEARCH_REPORT": sd / "long_short_research_report.json",
    }
    for key, path in paths.items():
        os.environ.setdefault(key, str(path))

    # Keep the current live behavior but make each watcher a short, refreshable
    # session. ALIGN_TO_5M makes it stop at the next 5m boundary and reload plans.
    os.environ.setdefault("LS_LIVE_POLL_SECONDS", "15")
    os.environ.setdefault("LS_LIVE_RUN_SECONDS", "420")
    os.environ.setdefault("LS_ALIGN_TO_5M", "1")
    os.environ.setdefault("LS_ALIGN_GRACE_SECONDS", "4")
    os.environ.setdefault("LS_LIVE_MAX_WATCH", "12")
    os.environ.setdefault("LS_REVERSAL_MAX_WATCH", "12")

    # Preserve the frozen V1.9 live universe unless explicitly versioned later.
    # Keep the production default stable; allow an explicit operator override.
    os.environ.setdefault("LS_MAX_SYMBOLS", "30")
    # Cap the second-stage preselection consistently with the requested
    # deep-scan budget. This does not weaken any trade safety gates.
    os.environ.setdefault("LS_PRESELECT_MAX", "24")
    os.environ.setdefault("LS_MIN_24H_QUOTE_VOL", "25000000")
    return sd


def set_status(name: str, **updates) -> None:
    with STATUS_LOCK:
        row = STATUS.setdefault(name, {})
        row.update(updates)
        row["updated_at_utc"] = now_iso()


def run_child(name: str, script: str) -> int:
    if STOP.is_set():
        return 0
    cmd = [sys.executable, "-u", str(ROOT / script)]
    started = time.time()
    set_status(name, state="STARTING", script=script, started_at_utc=now_iso(), pid=None)
    print(f"[daemon] START {name}: {' '.join(cmd)}", flush=True)
    proc = subprocess.Popen(cmd, cwd=str(ROOT), env=os.environ.copy())
    set_status(name, state="RUNNING", pid=proc.pid)
    try:
        while not STOP.is_set():
            rc = proc.poll()
            if rc is not None:
                elapsed = time.time() - started
                set_status(name, state="EXITED", returncode=rc, elapsed_seconds=round(elapsed, 1), pid=None)
                print(f"[daemon] EXIT {name}: rc={rc} elapsed={elapsed:.1f}s", flush=True)
                return int(rc)
            STOP.wait(1.0)
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        set_status(name, state="STOPPED", returncode=proc.returncode, pid=None)
        return int(proc.returncode or 0)
    except Exception:
        if proc.poll() is None:
            proc.kill()
        raise


def sleep_until_next_boundary(period: int, grace: float = 0.0) -> None:
    while not STOP.is_set():
        now = time.time()
        target = (math.floor(now / period) + 1) * period + grace
        wait = max(0.2, target - now)
        if STOP.wait(wait):
            return
        return


def analyst_loop() -> None:
    while not STOP.is_set():
        rc = run_child("analyst", "long_short_analyst.py")
        if STOP.is_set():
            return
        if rc != 0:
            print(f"[daemon] analyst failed rc={rc}; retrying after {RESTART_BACKOFF}s", flush=True)
            if STOP.wait(RESTART_BACKOFF):
                return
        else:
            sleep_until_next_boundary(ANALYST_INTERVAL, BOUNDARY_GRACE)


def watcher_loop(name: str, script: str) -> None:
    while not STOP.is_set():
        started = time.time()
        rc = run_child(name, script)
        if STOP.is_set():
            return
        elapsed = time.time() - started
        # Normal aligned exits are expected. Fast exits usually mean missing
        # state/API failure, so avoid a hot restart loop.
        if rc != 0 or elapsed < 8:
            if STOP.wait(RESTART_BACKOFF):
                return


def research_loop() -> None:
    if STOP.wait(RESEARCH_INITIAL_DELAY):
        return
    while not STOP.is_set():
        rc_v2 = run_child("v2_shadow", "long_short_v2_shadow.py")
        # Optional read-only venue health probe; never affects signal decisions.
        if os.getenv("LS_BYBIT_PAPER_PROBE_ENABLED", "0") == "1":
            rc_probe = subprocess.run(
                [sys.executable, "-u", str(ROOT / "long_short_data_router.py"),
                 "BTCUSDT", "bybit-paper"],
                cwd=str(ROOT), env=os.environ.copy(), timeout=45,
                capture_output=True, text=True, check=False,
            )
            print("[daemon] BYBIT_PAPER_PROBE rc=" + str(rc_probe.returncode) +
                  " output=" + (rc_probe.stdout or rc_probe.stderr)[-1200:], flush=True)
        rc_val = run_child("research_validation", "long_short_research_validation.py")
        set_status("research_cycle", state="DONE", v2_rc=rc_v2, validation_rc=rc_val)
        if STOP.wait(RESEARCH_INTERVAL):
            return


def backup_loop() -> None:
    """Backup runs independently; failure never stops analysis or changes signals."""
    if not os.getenv("LS_BACKUP_BUCKET"):
        print("[daemon] BACKUP_NOT_CONFIGURED", flush=True)
        return
    while not STOP.is_set():
        rc = run_child("daily_backup", "long_short_daily_backup.py")
        if rc != 0:
            print("[daemon] BACKUP_FAILED; retry in 1h", flush=True)
            if STOP.wait(3600):
                return
        elif STOP.wait(BACKUP_INTERVAL):
            return


def audit_loop() -> None:
    while not STOP.is_set():
        rc = run_child("operational_audit", "long_short_operational_audit.py")
        if rc != 0:
            print("[daemon] AUDIT_WARNING; review integrity and cohort", flush=True)
        if STOP.wait(3600):
            return


def heartbeat_loop(sd: Path) -> None:
    health_path = sd / "long_short_daemon_health.json"
    while not STOP.is_set():
        with STATUS_LOCK:
            snapshot = {
                "version": "LONG_SHORT_DAEMON_V1_2026-10-06",
                "time_utc": now_iso(),
                "telegram_configured": bool((os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()),
                "telegram_chat_configured": bool((os.getenv("TELEGRAM_CHAT_ID") or "").strip()),
                "telegram_chat_id_source": "environment" if (os.getenv("TELEGRAM_CHAT_ID") or "").strip() else "runtime_resolution_required",
                "state_dir": str(sd),
                "processes": json.loads(json.dumps(STATUS)),
            }
        try:
            health_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            print("[daemon] health write error", type(exc).__name__, str(exc)[:160], flush=True)
        print("[daemon] HEARTBEAT " + json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), flush=True)
        if STOP.wait(HEARTBEAT_SECONDS):
            return


def handle_signal(signum, _frame) -> None:
    print(f"[daemon] signal={signum}; shutting down", flush=True)
    STOP.set()


def main() -> int:
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    sd = configure_state_paths()
    token_ok = bool((os.getenv("TELEGRAM_BOT_TOKEN") or "").strip())
    chat_ok = bool((os.getenv("TELEGRAM_CHAT_ID") or "").strip())
    print(
        f"[daemon] boot state_dir={sd} telegram_token={token_ok} telegram_chat_id={chat_ok}",
        flush=True,
    )
    if not token_ok:
        print("[daemon] WARNING TELEGRAM_BOT_TOKEN missing; alerts cannot be delivered.", flush=True)
    if not chat_ok:
        print("[daemon] WARNING TELEGRAM_CHAT_ID missing; auto-discovery will be attempted but explicit ID is preferred.", flush=True)

    # One initial analyst pass gives both live watchers a fresh plan before they
    # start. Failure is not fatal; the analyst loop will keep retrying.
    initial_rc = run_child("analyst_initial", "long_short_analyst.py")
    if initial_rc != 0:
        print(f"[daemon] initial analyst failed rc={initial_rc}; supervisors will retry.", flush=True)

    threads = [
        threading.Thread(target=analyst_loop, name="analyst-loop", daemon=True),
        threading.Thread(target=watcher_loop, args=("continuation", "long_short_live_pool.py"), name="continuation-loop", daemon=True),
        threading.Thread(target=watcher_loop, args=("reversal", "long_short_reversal_live.py"), name="reversal-loop", daemon=True),
        threading.Thread(target=research_loop, name="research-loop", daemon=True),
        threading.Thread(target=heartbeat_loop, args=(sd,), name="heartbeat-loop", daemon=True),
        threading.Thread(target=backup_loop, name="backup-loop", daemon=True),
        threading.Thread(target=audit_loop, name="audit-loop", daemon=True),
    ]
    for t in threads:
        t.start()

    while not STOP.is_set():
        STOP.wait(2.0)

    for t in threads:
        t.join(timeout=20)
    print("[daemon] shutdown complete", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
