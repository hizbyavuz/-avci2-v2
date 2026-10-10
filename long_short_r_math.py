"""Shared V3.1.1 LONG/SHORT cost-adjusted reward/risk. Paper analysis only."""
import math

def trade_net_r(direction, entry, stop, tp1, cost_pct):
    result = {"ok": False, "reason": "invalid_input", "geometry_ok": False, "reward_pct": None, "risk_pct": None, "net_r": None}
    try:
        e,s,t,c=map(float,(entry,stop,tp1,cost_pct))
    except (TypeError,ValueError,OverflowError):
        return result
    if not all(math.isfinite(v) for v in (e,s,t,c)) or min(e,s,t)<=0 or c<0:
        return result
    if direction=="LONG":
        geometry=s<e<t
        reward=(t-e)/e*100
        risk=(e-s)/e*100
    elif direction=="SHORT":
        geometry=t<e<s
        reward=(e-t)/e*100
        risk=(s-e)/e*100
    else:
        return result
    result.update(geometry_ok=geometry,reward_pct=reward,risk_pct=risk)
    if not geometry:
        result["reason"]="geometry_invalid"
        return result
    result.update(ok=True,reason="ok",net_r=(reward-c)/(risk+c))
    return result
