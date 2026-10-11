import json
from gate_v4_universe import gate_usdt_perpetual_universe

result = gate_usdt_perpetual_universe()
print("GATE_UNIVERSE_RESULT " + json.dumps({
    "eligible_count": len(result["eligible"]),
    "rejected": result["rejected"],
    "liquidity_floor_usdt": result["liquidity_floor_usdt"],
    "sample": result["eligible"][:5],
}), flush=True)
