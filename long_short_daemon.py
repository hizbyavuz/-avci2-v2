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
TELEGRAM_CHAT_SOURCE = "unresolved"

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
    # Versioned observational worker + one persistent notification per coin.
    # Core scoring, execution and risk thresholds are unchanged.
    if os.getenv('LS_EARLY_BREAKOUT_V1_ENABLED','0')=='1':
        os.environ.setdefault('LS_UNIFIED_CARD_ENABLED','1')
    enforce_futures_only()
    return sd


FUTURES_ONLY_ENV = {
    # Analyst: verified native USD-M candles only; no Spot/Bybit/Gate fallback.
    "LS_BINANCE_FUTURES_ONLY": "1",
    # Live pool: native USD-M candles + price. Spot snapshot path is forbidden.
    # Its existing native_futures_observation_only gate stays in force.
    "LS_NATIVE_FUTURES_SNAPSHOT": "1",
    # Reversal watcher reads Binance Spot candles: research log only, never Telegram.
    "LS_REVERSAL_TELEGRAM": "0",
}


def enforce_futures_only() -> dict:
    """Signal data must be Binance UM Futures only. Not configurable to Spot."""
    overridden = {}
    for key, value in FUTURES_ONLY_ENV.items():
        current = os.environ.get(key)
        if current is not None and current.strip() != value:
            overridden[key] = current
        os.environ[key] = value
    print("[daemon] FUTURES_ONLY_ENFORCED " + json.dumps(
        {"settings": FUTURES_ONLY_ENV, "ignored_env_overrides": overridden}), flush=True)
    return overridden


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
    # Read-only Bybit connectivity check is independent of the shadow scan.
    if os.getenv("LS_BYBIT_PAPER_PROBE_ENABLED", "0") == "1":
        try:
            probe = subprocess.run(
                [sys.executable, "-u", str(ROOT / "long_short_data_router.py"),
                 "BTCUSDT", "bybit-paper"],
                cwd=str(ROOT), env=os.environ.copy(), timeout=45,
                capture_output=True, text=True, check=False,
            )
            print("[daemon] BYBIT_PAPER_PROBE rc=" + str(probe.returncode) +
                  " output=" + (probe.stdout or probe.stderr)[-1200:], flush=True)
        except (subprocess.TimeoutExpired, OSError) as exc:
            print("[daemon] BYBIT_PAPER_PROBE_ERROR " + str(exc), flush=True)
    # Gate USDT perpetual read-only health probe; independent of Bybit access.
    try:
        gate_probe = subprocess.run(
            [sys.executable, "-u", str(ROOT / "long_short_data_router.py"),
             "BTCUSDT", "gate-paper"],
            cwd=str(ROOT), env=os.environ.copy(), timeout=45,
            capture_output=True, text=True, check=False,
        )
        print("[daemon] GATE_PAPER_PROBE rc=" + str(gate_probe.returncode) +
              " output=" + (gate_probe.stdout or gate_probe.stderr)[-1200:], flush=True)
    except (subprocess.TimeoutExpired, OSError) as exc:
        print("[daemon] GATE_PAPER_PROBE_ERROR " + str(exc), flush=True)
    # Inspect same-venue OI/funding/taker/long-short/depth coverage; no trade approval.
    try:
        gate_deriv = subprocess.run(
            [sys.executable, "-u", str(ROOT / "long_short_data_router.py"),
             "BTCUSDT", "gate-derivatives"],
            cwd=str(ROOT), env=os.environ.copy(), timeout=45,
            capture_output=True, text=True, check=False,
        )
        print("[daemon] GATE_DERIVATIVES_PROBE rc=" + str(gate_deriv.returncode) +
              " output=" + (gate_deriv.stdout or gate_deriv.stderr)[-3500:], flush=True)
    except (subprocess.TimeoutExpired, OSError) as exc:
        print("[daemon] GATE_DERIVATIVES_PROBE_ERROR " + str(exc), flush=True)
    # Cross-symbol Gate futures coverage check, read-only and fail-closed.
    try:
        gate_batch = subprocess.run(
            [sys.executable, "-u", str(ROOT / "long_short_data_router.py"),
             "BTCUSDT", "gate-batch"],
            cwd=str(ROOT), env=os.environ.copy(), timeout=150,
            capture_output=True, text=True, check=False,
        )
        print("[daemon] GATE_BATCH_PROBE rc=" + str(gate_batch.returncode) +
              " output=" + (gate_batch.stdout or gate_batch.stderr)[-6500:], flush=True)
    except (subprocess.TimeoutExpired, OSError) as exc:
        print("[daemon] GATE_BATCH_PROBE_ERROR " + str(exc), flush=True)
    if STOP.wait(RESEARCH_INITIAL_DELAY):
        return
    while not STOP.is_set():
        rc_v2 = run_child("v2_shadow", "long_short_v2_shadow.py")
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
        run_child("backup_inventory", "long_short_backup_inventory.py")
        rc = run_child("daily_backup", "long_short_daily_backup.py")
        if rc == 0:
            run_child("backup_retention", "long_short_backup_retention.py")
        if rc != 0:
            print("[daemon] BACKUP_FAILED; retry in 1h", flush=True)
            if STOP.wait(3600):
                return
        elif STOP.wait(BACKUP_INTERVAL):
            return


def audit_loop() -> None:
    while not STOP.is_set():
        run_child("storage_audit", "long_short_storage_audit.py")
        rc = run_child("operational_audit", "long_short_operational_audit.py")
        if rc != 0:
            print("[daemon] AUDIT_WARNING; review integrity and cohort", flush=True)
        if STOP.wait(3600):
            return


def data_health_loop(sd: Path) -> None:
    """Read-only coverage/readiness/outcome report every 15 minutes."""
    interval = int(os.getenv("LS_DATA_HEALTH_INTERVAL_SECONDS", "900"))
    if STOP.wait(120):
        return
    while not STOP.is_set():
        try:
            from long_short_data_health import build, summary_line
            report = build(str(sd))
            (sd / "long_short_data_health.json").write_text(
                json.dumps(report, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            print("[daemon] DATA_HEALTH " + json.dumps(summary_line(report), sort_keys=True), flush=True)
            print("[daemon] SIGNAL_HEALTH " + json.dumps(report.get("signals"), sort_keys=True), flush=True)
        except Exception as exc:
            print("[daemon] DATA_HEALTH_ERROR", type(exc).__name__, str(exc)[:160], flush=True)
        if STOP.wait(interval):
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
                "telegram_chat_id_source": TELEGRAM_CHAT_SOURCE,
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
    global TELEGRAM_CHAT_SOURCE
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    sd = configure_state_paths()
    token_ok = bool((os.getenv("TELEGRAM_BOT_TOKEN") or "").strip())
    chat_ok = bool((os.getenv("TELEGRAM_CHAT_ID") or "").strip())
    TELEGRAM_CHAT_SOURCE = "environment" if chat_ok else "unresolved"
    print(
        f"[daemon] boot state_dir={sd} telegram_token={token_ok} telegram_chat_id={chat_ok}",
        flush=True,
    )
    if not token_ok:
        print("[daemon] WARNING TELEGRAM_BOT_TOKEN missing; alerts cannot be delivered.", flush=True)
    if not chat_ok:
        print("[daemon] WARNING TELEGRAM_CHAT_ID missing; auto-discovery will be attempted but explicit ID is preferred.", flush=True)
        # Resolve once at boot and share the verified destination with all child
        # processes. Never guess a chat ID or print the token / destination.
        try:
            from binance_notify import resolve_chat_id
            resolved = resolve_chat_id(
                os.environ.get("TELEGRAM_BOT_TOKEN", ""), "",
                db_path=str(sd / "long_short_telegram_chat_cache.db"),
            ) if token_ok else ""
            if resolved:
                os.environ["TELEGRAM_CHAT_ID"] = str(resolved)
                chat_ok = True
                TELEGRAM_CHAT_SOURCE = "runtime_resolved"
                print("[daemon] TELEGRAM_CHAT_RESOLVED_AT_BOOT", flush=True)
            else:
                print("[daemon] TELEGRAM_CHAT_UNRESOLVED_AT_BOOT", flush=True)
        except Exception as exc:
            print("[daemon] TELEGRAM_CHAT_RESOLUTION_ERROR", type(exc).__name__, flush=True)

    # One-shot read-only Coinalyze connectivity check; never blocks trading loops.
    # Prints only market identity, freshness and whether values are available.
    def coinalyze_boot_probe():
        try:
            from coinalyze_binance_probe import probe
            result = probe(["BTCUSDT", "ETHUSDT"])
            safe = {symbol: {
                "market_verified": data.get("market_verified", False),
                "oi_ok": data.get("oi_ok", False),
                "ls_ok": data.get("ls_ok", False),
                "oi_age_seconds": data.get("oi_age_seconds"),
                "ls_age_seconds": data.get("ls_age_seconds"),
                "oi_error": data.get("oi_error"),
                "ls_error": data.get("ls_error"),
            } for symbol, data in result.items()}
            print("[daemon] COINALYZE_BINANCE_PROBE " + json.dumps(safe), flush=True)
        except Exception as exc:
            print("[daemon] COINALYZE_BINANCE_PROBE_FAILED " + type(exc).__name__, flush=True)

    if os.getenv('LS_NATIVE_CHAIN_V2','0')!='1':
        threading.Thread(target=coinalyze_boot_probe, name="coinalyze-boot-probe", daemon=True).start()

    # Observe Coinalyze Binance-only OI history without changing any signal.
    # Only two symbols at startup: avoids consuming the provider's 40/min quota.
    def coinalyze_shadow_check():
        try:
            from long_short_coinalyze_adapter import snapshot
            for symbol in ("BTCUSDT", "ETHUSDT"):
                result = snapshot(symbol)
                safe = {
                    "symbol": symbol,
                    "source": result.get("source"),
                    "oi_fresh": result.get("oi_fresh", False),
                    "oi_change_1h_available": result.get("oi_change_1h") is not None,
                    "ls_fresh": result.get("ls_fresh", False),
                    "oi_unit": result.get("oi_unit"),
                    "error_type": result.get("error_type"),
                }
                print("[daemon] COINALYZE_SHADOW " + json.dumps(safe), flush=True)
        except Exception as exc:
            print("[daemon] COINALYZE_SHADOW_FAILED " + type(exc).__name__, flush=True)

    if os.getenv('LS_NATIVE_CHAIN_V2','0')!='1':
        threading.Thread(target=coinalyze_shadow_check, name="coinalyze-shadow", daemon=True).start()

    # Funding shadow: two Binance-only symbols; no scoring, orders or DB writes.
    def coinalyze_funding_shadow():
        try:
            from long_short_coinalyze_funding_probe import funding_snapshot
            for symbol in ("BTCUSDT", "ETHUSDT"):
                try:
                    result = funding_snapshot(symbol)
                    safe = {
                        "symbol": symbol,
                        "source": result.get("source"),
                        "funding_fresh": result.get("funding_fresh", False),
                        "funding_available": result.get("funding_pct") is not None,
                        "age_seconds": result.get("funding_age_seconds"),
                    }
                    print("[daemon] COINALYZE_FUNDING_SHADOW " + json.dumps(safe), flush=True)
                except Exception as exc:
                    print("[daemon] COINALYZE_FUNDING_SHADOW_FAILED " + symbol + " " + type(exc).__name__, flush=True)
        except Exception as exc:
            print("[daemon] COINALYZE_FUNDING_SHADOW_FAILED " + type(exc).__name__, flush=True)

    if os.getenv('LS_NATIVE_CHAIN_V2','0')!='1':
        threading.Thread(target=coinalyze_funding_shadow, name="coinalyze-funding-shadow", daemon=True).start()

    # Read-only Binance Futures all-market ticker collector; no signal changes.
    if os.getenv("LS_FUTURES_UNIVERSE_SHADOW_ENABLED", "1") == "1":
        threading.Thread(
            target=watcher_loop,
            args=("binance_futures_universe", "long_short_futures_universe_worker.py"),
            name="binance-futures-universe-loop", daemon=True,
        ).start()

    # Continuous, storage-bounded taker observation; independent of production signals.
    # Operator may disable via LS_TAKER_SHADOW_ENABLED=0.
    if os.getenv("LS_TAKER_SHADOW_ENABLED", "1") == "1":
        threading.Thread(
            target=watcher_loop,
            args=("binance_taker_shadow", "long_short_taker_shadow_worker.py"),
            name="binance-taker-shadow-loop", daemon=True,
        ).start()

    # Persistent native Binance Futures 1m/5m closed candles, shadow only.
    # Never promote to signal price until freshness, coverage and provenance pass.
    if os.getenv("LS_FUTURES_KLINE_SHADOW_ENABLED", "1") == "1":
        threading.Thread(
            target=watcher_loop,
            args=("binance_futures_kline_shadow", "long_short_futures_kline_worker.py"),
            name="binance-futures-kline-shadow-loop", daemon=True,
        ).start()

    # Read-only end-to-end Binance-only derivative coverage audit.
    # Does not change signals; logs exactly which inputs remain missing.
    def binance_derivative_coverage_audit():
        if STOP.wait(420):
            return
        try:
            from long_short_coinalyze_adapter import snapshot
            from long_short_coinalyze_funding_probe import funding_snapshot
            from long_short_taker_continuous_shadow import window
            from long_short_binance_derivative_gate import assess
            for symbol in ("BTCUSDT", "ETHUSDT"):
                oi=snapshot(symbol)
                funding=funding_snapshot(symbol)
                try:
                    taker=window(str(sd / "long_short_taker_minute_shadow.db"),symbol)
                except Exception as exc:
                    taker={"ready":False,"source":None,"error":type(exc).__name__}
                evidence=dict(oi)
                evidence["funding_fresh"]=bool(funding.get("funding_fresh"))
                evidence["taker_fresh"]=bool(taker.get("ready"))
                evidence["taker_coverage_ready"]=bool(taker.get("ready"))
                gate=assess(evidence,funding_pct=funding.get("funding_pct"),
                            taker_ratio=taker.get("taker_ratio"),
                            funding_source=funding.get("source"),
                            taker_source=taker.get("source"))
                print("[daemon] BINANCE_ONLY_DERIVATIVE_AUDIT "+
                      json.dumps({"symbol":symbol,"ready":gate["ready"],
                                  "missing":gate["missing"],
                                  "oi_fresh":oi.get("oi_fresh"),
                                  "funding_fresh":funding.get("funding_fresh"),
                                  "taker_ready":taker.get("ready"),
                                  "ls_fresh":oi.get("ls_fresh")}),flush=True)
        except Exception as exc:
            print("[daemon] BINANCE_ONLY_DERIVATIVE_AUDIT_ERROR "+
                  type(exc).__name__+":"+str(exc)[:160],flush=True)
    if os.getenv("LS_BINANCE_DERIVATIVE_AUDIT_ENABLED","1")=="1":
        threading.Thread(target=binance_derivative_coverage_audit,
                         name="binance-derivative-coverage",daemon=True).start()

    if os.getenv('LS_NATIVE_CHAIN_V2','0')=='1':
        if os.getenv('LS_EARLY_BREAKOUT_V1_ENABLED','0')=='1':
            threading.Thread(target=watcher_loop,
                args=('early_breakout_observer','long_short_breakout_observer.py'),
                name='early-breakout-observer-v1',daemon=True).start()
        if os.getenv('LS_DATA_OPPORTUNITY_V2','0')=='1':
            threading.Thread(target=watcher_loop,
                args=('data_opportunity_research','long_short_data_opportunity_v2.py'),
                name='data-opportunity-research-v2',daemon=True).start()
        threading.Thread(target=watcher_loop,
            args=('binance_native_derivatives','long_short_native_derivatives.py'),
            name='native-derivatives-v2',daemon=True).start()
        if os.getenv('LS_NATIVE_V2_VERIFY','0')=='1':
            threading.Thread(target=run_child,args=('native_transport_check','long_short_native_verify.py'),
                name='native-transport-check',daemon=True).start()

    # Optional closed-candle repair via private official Binance Futures relay.
    # Separate worker keeps history requests off the live WebSocket thread.
    if os.getenv("LS_FUTURES_HISTORY_RELAY_URL", ""):
        threading.Thread(
            target=watcher_loop,
            args=("binance_futures_history", "long_short_futures_history_worker.py"),
            name="binance-futures-history-loop", daemon=True,
        ).start()

    # Single bounded read-only native taker connectivity check at startup.
    # Stores records in the existing persistent volume; never sends orders or alerts.
    def native_taker_boot_probe():
        try:
            from long_short_v4_native_pipeline import observe
            from long_short_native_taker_health import check
            path = str(sd / "long_short_v4_native.db")
            outcome = observe(path, ["BTCUSDT", "ETHUSDT"], 30)
            health = {symbol: check(path, symbol) for symbol in ("BTCUSDT", "ETHUSDT")}
            import sqlite3
            with sqlite3.connect(path, timeout=5) as depth_db:
                depth_rows = depth_db.execute("SELECT symbol, COUNT(*), MAX(received_ms) FROM raw_events WHERE stream LIKE ? GROUP BY symbol", ("%depth20%",)).fetchall()
            safe = {
                "state": outcome.get("state"),
                "accepted": outcome.get("accepted"),
                "market_connected": outcome.get("health", {}).get("market"),
                "public_connected": outcome.get("health", {}).get("public"),
                "taker": {symbol: {"ready": row["ready"],
                                    "reason": row["reason"],
                                    "trade_count_2m": row["trade_count_2m"]}
                          for symbol, row in health.items()},
                "continuous_coverage": False,
                "depth20_events": {sym: count for sym, count, _ in depth_rows},
            }
            print("[daemon] BINANCE_NATIVE_TAKER_BOOT " + json.dumps(safe), flush=True)
        except Exception as exc:
            print("[daemon] BINANCE_NATIVE_TAKER_BOOT_FAILED " + type(exc).__name__, flush=True)

    # V4 native probe is observational only. Avoid further disk writes when the
    # persistent volume is near capacity; never alter signal/derivative gates.
    import shutil
    try:
        disk = shutil.disk_usage(sd)
        pressure = disk.total > 0 and disk.used / disk.total >= 0.85
    except OSError:
        pressure = True  # fail safe for optional research writer
    if pressure:
        print("[daemon] OPTIONAL_NATIVE_BOOT_SKIPPED_STORAGE_PRESSURE", flush=True)
    else:
        threading.Thread(target=native_taker_boot_probe, name="native-taker-boot", daemon=True).start()

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
        threading.Thread(target=data_health_loop, args=(sd,), name="data-health-loop", daemon=True),
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
