"""Pure, non-invasive diagnostics for why directional setups are blocked.

Never authorizes a trade. JSONL events are append-only in the persistent state dir.
"""
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def record_setup_gates(symbol, setup):
    """Record one V3 decision; keep data failures visible, never raise."""
    try:
        gates = setup.get("gates") or {}
        failed = [str(key) for key, ok in gates.items() if not ok]
        reasons = setup.get("veto_reasons") or failed
        event = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "symbol": str(symbol),
            "direction": setup.get("direction", "NONE"),
            "setup_type": setup.get("setup_type", "NONE"),
            "eligible": bool(setup.get("eligible")),
            "veto_reasons": reasons,
            "gates": gates,
            "phase": setup.get("phase"),
        }
        path = Path(os.getenv("LS_STATE_DIR") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or ".") / "signal_gate_audit.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        print("SIGNAL_GATE_AUDIT", json.dumps({
            "symbol": event["symbol"], "direction": event["direction"],
            "eligible": event["eligible"], "veto": reasons,
        }, ensure_ascii=False), flush=True)
    except Exception as exc:
        print("SIGNAL_GATE_AUDIT_ERROR", type(exc).__name__, str(exc)[:160], flush=True)
