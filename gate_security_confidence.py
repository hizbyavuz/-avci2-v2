"""Gate security-confidence evidence layer.

This is NOT a safety guarantee and never changes frozen V5 membership.
It separates:
- hard veto: concrete dangerous evidence
- confidence: how complete/consistent the available safety evidence is
"""
import json
import sqlite3
import time

VERSION = "gate-security-confidence-v1-20260924"

HARD_TOKENS = (
    "HONEYPOT", "BLACKLIST", "SELFDESTRUCT", "OWNER_CHANGE_BALANCE",
    "CAN_TAKE_BACK_OWNERSHIP", "MINT_AUTHORITY_ACTIVE",
    "FREEZE_AUTHORITY_ACTIVE", "TOP1_CONCENTRATION", "TOP5_CONCENTRATION",
    "TOP10_CONCENTRATION", "EXIT_1K_LOSS_HIGH", "EXIT_5K_LOSS_HIGH",
    "SELL_TAX_HIGH", "BUY_TAX_HIGH", "LP_LOW_PROTECTION",
    "BUNDLE_SNIPER_PROXY",
)

def _ok_quote(q, maximum):
    return bool(q and q.get("ok") and q.get("loss_pct") is not None
                and float(q["loss_pct"]) <= maximum)

def assess(item):
    flags=[str(x) for x in (item.get("security_risk_reasons") or [])]
    hard=[f for f in flags if any(token in f.upper() for token in HARD_TOKENS)]
    cluster=item.get("trade_cluster") or {}
    if cluster.get("wash_proxy") is True:
        hard.append("WASH_PROXY")
    creator=item.get("creator_reputation") or {}
    if creator.get("status")=="FLAGGED":
        hard.append("CREATOR_FLAGGED")

    checks=[]
    def add(name, status, detail=None):
        checks.append({"name":name,"status":status,"detail":detail})

    network=item.get("network_id")
    contract=item.get("token_contract")
    add("contract_identity", "PASS" if network and contract else "UNKNOWN")

    if network=="solana":
        sec=item.get("solana_security") or {}
        if sec.get("ok"):
            mint_ok=sec.get("mint_authority_active") is False
            freeze_ok=sec.get("freeze_authority_active") is False
            add("mint_freeze", "PASS" if mint_ok and freeze_ok else "FAIL")
        else:
            add("mint_freeze","UNKNOWN")
        q1=item.get("exit_1k") or {}; q5=item.get("exit_5k") or {}
    else:
        sec=item.get("evm_security") or {}
        if sec.get("ok"):
            dangerous=("is_honeypot","hidden_owner","can_take_back_ownership",
                       "owner_change_balance","selfdestruct","is_blacklisted")
            vals=[sec.get(k) for k in dangerous]
            add("contract_security",
                "PASS" if all(v is False for v in vals) else
                ("FAIL" if any(v is True for v in vals) else "UNKNOWN"))
            add("open_source",
                "PASS" if sec.get("is_open_source") is True else
                ("FAIL" if sec.get("is_open_source") is False else "UNKNOWN"))
        else:
            add("contract_security","UNKNOWN")
            add("open_source","UNKNOWN")
        q1=item.get("evm_exit_1k") or {}; q5=item.get("evm_exit_5k") or {}

    if q1.get("ok") or q5.get("ok"):
        add("sellability",
            "PASS" if _ok_quote(q1,7) and _ok_quote(q5,12) else "FAIL",
            {"loss_1k":q1.get("loss_pct"),"loss_5k":q5.get("loss_pct")})
    else:
        add("sellability","UNKNOWN")

    holder=item.get("adjusted_holder") or {}
    top10=holder.get("top10_pct")
    if holder.get("ok") and top10 is not None:
        add("holder_distribution","PASS" if float(top10)<85 else "FAIL",top10)
    else:
        # Solana native holder profile can still provide evidence.
        native=item.get("solana_security") or {}
        n10=native.get("top10_pct")
        add("holder_distribution",
            "PASS" if n10 is not None and float(n10)<85 else
            ("FAIL" if n10 is not None else "UNKNOWN"), n10)

    lp=item.get("lp_protection") or {}
    protected=lp.get("protected_pct")
    if protected is not None:
        add("lp_protection","PASS" if float(protected)>=50 else "FAIL",protected)
    else:
        add("lp_protection","UNKNOWN")

    if creator.get("status") in ("NO_FINDING","FLAGGED"):
        add("creator_reputation","PASS" if creator.get("status")=="NO_FINDING" else "FAIL")
    else:
        add("creator_reputation","UNKNOWN")

    if cluster.get("wash_proxy") is not None:
        add("wash_proxy","PASS" if cluster.get("wash_proxy") is False else "FAIL")
    else:
        add("wash_proxy","UNKNOWN")

    known=[x for x in checks if x["status"]!="UNKNOWN"]
    passed=[x for x in checks if x["status"]=="PASS"]
    failed=[x for x in checks if x["status"]=="FAIL"]
    coverage=len(known)/len(checks) if checks else 0.0
    pass_rate=len(passed)/len(known) if known else 0.0

    if hard:
        label="BLOCKED"
    elif len(known)>=6 and coverage>=0.70 and pass_rate>=0.85 and not failed:
        label="STRONG"
    elif len(known)>=4 and coverage>=0.50 and pass_rate>=0.70:
        label="MEDIUM"
    else:
        label="WEAK"

    return {
        "version":VERSION,
        "label":label,
        "coverage_pct":round(coverage*100,1),
        "pass_rate_pct":round(pass_rate*100,1),
        "known_checks":len(known),
        "total_checks":len(checks),
        "hard_veto":bool(hard),
        "hard_veto_reasons":sorted(set(hard)),
        "checks":checks,
    }

def label_tr(value):
    return {"WEAK":"ZAYIF","MEDIUM":"ORTA","STRONG":"GÜÇLÜ",
            "BLOCKED":"BLOKLU"}.get(value,value or "BİLİNMİYOR")

def one_line(item):
    result=item.get("security_confidence") or assess(item)
    text=f"Güvenlik güveni: {label_tr(result['label'])} • kanıt kapsamı %{result['coverage_pct']:.0f}"
    if result["hard_veto"]:
        text += " • HARD VETO"
    return text

def record(path,batch_id,items):
    with sqlite3.connect(path,timeout=30) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS gate_security_confidence_history(
            batch_id TEXT NOT NULL,scan_ts INTEGER NOT NULL,network_id TEXT NOT NULL,
            token_contract TEXT NOT NULL,validation_event_id INTEGER,
            version TEXT NOT NULL,label TEXT NOT NULL,coverage_pct REAL NOT NULL,
            pass_rate_pct REAL NOT NULL,hard_veto INTEGER NOT NULL,
            hard_veto_reasons_json TEXT NOT NULL,checks_json TEXT NOT NULL,
            PRIMARY KEY(batch_id,network_id,token_contract,version))""")
        scan=con.execute("SELECT scan_ts FROM gate_scan_health WHERE batch_id=?",(batch_id,)).fetchone()
        ts=int(scan[0]) if scan else int(time.time())
        n=0
        for item in items:
            network=item.get("network_id"); contract=item.get("token_contract")
            if not network or not contract:
                continue
            result=item.get("security_confidence") or assess(item)
            con.execute("""INSERT OR REPLACE INTO gate_security_confidence_history
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (batch_id,ts,network,contract,item.get("validation_event_id"),
                 VERSION,result["label"],result["coverage_pct"],result["pass_rate_pct"],
                 int(result["hard_veto"]),json.dumps(result["hard_veto_reasons"]),
                 json.dumps(result["checks"])))
            n+=1
        con.commit()
        return n
