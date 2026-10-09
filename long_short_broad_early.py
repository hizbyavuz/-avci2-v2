"""Broad-universe early-movement observer; NOT an entry signal.

Uses only the prefilter's already-fetched, closed 5m/15m candles. Writes
append-only observations for subsequent forward outcome analysis.
No Telegram, trade authorization, or frozen V3 thresholds are modified.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def observe(preselected, selected):
    selected_symbols = {x["symbol"] for x in selected}
    path = Path(os.getenv("LS_STATE_DIR") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or ".long-short-state") / "broad_early_observations.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    counts = {"LONG": 0, "SHORT": 0, "NONE": 0}\n    observations = []
    ts = datetime.now(timezone.utc).isoformat()
    with path.open("a", encoding="utf-8") as out:
        for x in preselected:
            try:
                t5, t15 = x["t5"], x["t15"]
                p = float(t5["price"])
                a = max(float(t5["atr"]), p * 0.0001)
                vm = float(t5["vol_mult"])
                change = float(t5["change_4"])
                structure = int(t15["structure"])
                near_high = abs(float(t5["dist_high20_pct"])) <= max(0.15, float(t5["atr_pct"]))
                near_low = abs(float(t5["dist_low20_pct"])) <= max(0.15, float(t5["atr_pct"]))
                up = structure >= 0 and p >= float(t5["ema20"]) and (near_high or bool(t5["breakout20"])) and vm >= 1.10 and change > 0
                down = structure <= 0 and p <= float(t5["ema20"]) and (near_low or bool(t5["breakdown20"])) and vm >= 1.10 and change < 0
                direction = "LONG" if up and not down else "SHORT" if down and not up else "NONE"
                extended = abs(change) > 2.5 * float(t5["atr_pct"])
                state = "LATE_CHASE" if direction != "NONE" and extended else "EARLY_WATCH" if direction != "NONE" else "NO_EARLY_PATTERN"
                counts[direction] += 1
                item = {
                    "timestamp_utc": ts, "symbol": x["symbol"], "price": p,
                    "direction": direction, "state": state,
                    "deep_selected": x["symbol"] in selected_symbols,
                    "quote_volume": x.get("quote_volume"), "prefilter_rank": x.get("rank"),
                    "volume_mult_5m": vm, "change_4_5m_pct": change,
                    "atr_pct_5m": t5["atr_pct"], "structure_15m": structure,
                    "near_high_5m": near_high, "near_low_5m": near_low,
                    "closed_candle_only": True, "observer_version": "EARLY_OBSERVER_V1",
                }
                observations.append(item)
                out.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")
            except Exception as exc:
                print("EARLY_OBSERVER_ROW_ERROR", x.get("symbol"), type(exc).__name__, str(exc)[:100], flush=True)
    print("BROAD_EARLY_OBSERVER", json.dumps({"total": len(preselected), "deep": len(selected), "directions": counts, "file": str(path)}), flush=True)

    try:
        from long_short_early_forward import track
        track(observations)
    except Exception as exc:
        print('EARLY_FORWARD_TRACK_ERROR', type(exc).__name__, str(exc)[:160], flush=True)
