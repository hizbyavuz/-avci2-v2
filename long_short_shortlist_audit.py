"""Append-only observability for preselection; never changes trading decisions."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

def record_shortlist(preselected, selected, *, limit, volume_floor):
    try:
        selected_symbols={str(x.get("symbol")) for x in selected}
        rows=[]
        for x in preselected:
            meta=x.get("discovery_meta") or {}
            volume=float(x.get("quote_volume") or 0)
            reasons=[]
            if volume < volume_floor: reasons.append("volume_below_execution_floor")
            if not meta.get("binance_spot_member"): reasons.append("not_binance_spot_member")
            if meta.get("external_only_unverified"): reasons.append("external_unverified")
            if str(x.get("symbol")) not in selected_symbols:
                reasons.append("shortlist_capacity_or_rank")
            rows.append({"symbol":x.get("symbol"),"rank":x.get("rank"),"quote_volume":volume,
                         "selected":str(x.get("symbol")) in selected_symbols,
                         "reasons":reasons,"discovery":x.get("v3_discovery")})
        path=Path(os.getenv("LS_STATE_DIR") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or ".")/"shortlist_audit.jsonl"
        path.parent.mkdir(parents=True,exist_ok=True)
        event={"timestamp_utc":datetime.now(timezone.utc).isoformat(),"limit":limit,
               "volume_floor":volume_floor,"preselected_count":len(rows),
               "selected_count":len(selected),"rows":rows}
        with path.open("a",encoding="utf-8") as f:
            f.write(json.dumps(event,ensure_ascii=False,default=str)+"\n")
        print("SHORTLIST_AUDIT",json.dumps({"preselected":len(rows),"selected":len(selected),
              "rejected":len(rows)-len(selected),"file":str(path)},ensure_ascii=False),flush=True)
    except Exception as exc:
        print("SHORTLIST_AUDIT_ERROR",type(exc).__name__,str(exc)[:160],flush=True)
