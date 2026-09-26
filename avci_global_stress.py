#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Simple institutional stress overlay for the combined paper portfolio.

Uses current reported BTC/SOL beta and cost stress multipliers. This is a
scenario diagnostic, not a forecast or VaR model.
"""
import json, os
from datetime import datetime, timezone

IN=os.getenv("GLOBAL_REPORT","avci_global_governance.json")
OUT="avci_global_stress.json"
SCENARIOS=[
 {"name":"BTC_SHOCK","btc_pct":-10.0,"sol_pct":-15.0,"cost_multiplier":1.5},
 {"name":"ALT_RISK_OFF","btc_pct":-15.0,"sol_pct":-25.0,"cost_multiplier":2.0},
 {"name":"LIQUIDITY_CRUNCH","btc_pct":-5.0,"sol_pct":-8.0,"cost_multiplier":3.0},
]
def main():
    if not os.path.exists(IN):
        print("global report missing");return
    r=json.load(open(IN,encoding="utf-8"))
    bb=r.get("btc_beta");sb=r.get("sol_beta")
    out=[]
    for s in SCENARIOS:
        shock=None
        if bb is not None and sb is not None:
            # Conservative blended proxy to avoid double-counting full exposures.
            shock=.5*float(bb)*s["btc_pct"]+.5*float(sb)*s["sol_pct"]
        out.append({**s,"beta_proxy_portfolio_shock_pct":shock,
                    "note":"beta-based stress proxy; not a forecast and not a replacement for path simulation"})
    report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),
            "btc_beta":bb,"sol_beta":sb,"scenarios":out,
            "status":"OBSERVATIONAL_STRESS_PROXY"}
    json.dump(report,open(OUT,"w",encoding="utf-8"),ensure_ascii=False,indent=2)
    print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=="__main__":main()
