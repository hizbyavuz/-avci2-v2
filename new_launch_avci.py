#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NEW LAUNCH AVCI — isolated early-launch observer.

This motor is intentionally independent from Binance Avci and Gate Web3 Avci:
- own SQLite DB
- own workflow/concurrency group
- no imports from scanner.py or Gate/Binance state
- paper/observation only; never places orders

Goal: inspect very new on-chain pools before they become large runners, while
failing closed on weak liquidity/security and preserving every observation.
"""
import json
import math
import os
import sqlite3
import time
from datetime import datetime, timezone

import requests

DB = os.getenv("NEW_LAUNCH_DB", ".new-launch-state/new_launch_avci.db")
NOTIFY_REAPPEAR_HOURS = 6

def init_notification_state(c):
    c.execute("""CREATE TABLE IF NOT EXISTS new_launch_notification_state(
      network TEXT NOT NULL,contract TEXT NOT NULL,last_class TEXT,last_score REAL,
      last_seen_ts INTEGER NOT NULL,last_notified_ts INTEGER,
      PRIMARY KEY(network,contract)
    )""")

def should_notify_candidate(c,ts,row,d):
    """Telegram-only dedupe. Scanner observations and signal counters remain untouched."""
    init_notification_state(c)
    key=(row["network"],row["contract"])
    old=c.execute("""SELECT * FROM new_launch_notification_state
        WHERE network=? AND contract=?""",key).fetchone()
    event=None
    if old is None:
        event="NEW"
    else:
        gap_h=max(0.0,(ts-int(old["last_seen_ts"] or ts))/3600.0)
        if gap_h>=NOTIFY_REAPPEAR_HOURS:
            event="REIGNITED"
        elif str(old["last_class"] or "")!=str(d["classification"]):
            event="CHANGED"
        elif old["last_score"] is not None and float(d["score"])-float(old["last_score"])>=3:
            event="STRENGTHENED"
    notified=ts if event else (old["last_notified_ts"] if old else None)
    c.execute("""INSERT OR REPLACE INTO new_launch_notification_state
      (network,contract,last_class,last_score,last_seen_ts,last_notified_ts)
      VALUES(?,?,?,?,?,?)""",
      (row["network"],row["contract"],d["classification"],d["score"],ts,notified))
    return event

def notification_prefix(event):
    return {
        "NEW":"🆕 YENİ",
        "STRENGTHENED":"⬆️ GÜÇLENDİ",
        "REIGNITED":"🔥 YENİDEN CANLANDI",
        "CHANGED":"↔️ DURUM DEĞİŞTİ",
    }.get(event,"")

GECKO = "https://api.geckoterminal.com/api/v2"
NETWORKS = {
    "solana": "Solana",
    "base": "Base",
    "bsc": "BSC",
    "eth": "Ethereum",
    "arbitrum": "Arbitrum",
}
EVM_CHAIN_IDS = {"eth": "1", "bsc": "56", "arbitrum": "42161", "base": "8453"}
STABLES = {"USDT","USDC","USDE","USDS","DAI","FDUSD","TUSD","PYUSD","USDD","FRAX","GUSD","LUSD","USDP","USD0","USD1","WETH","WBTC","SOL","WSOL"}

# New motor rules. They do not modify or reuse frozen Gate/Binance thresholds.
MIN_AGE_MIN = 2.0
MAX_WATCH_AGE_MIN = 180.0
MAX_CANDIDATE_AGE_MIN = 90.0
MIN_LIQUIDITY = 15_000.0
MIN_VOLUME_5M = 500.0
MIN_TX_5M = 8
MIN_BUY_SELL_5M = 1.35
MAX_CANDIDATE_CHANGE = 90.0
LATE_CHANGE = 150.0
MIN_RETENTION = 70.0
MIN_SCORE_CANDIDATE = 7
# Anti-rug / stale-snapshot guardrails for this isolated New Launch motor.
# These do not touch Binance Avci or Gate Web3 Avci frozen rules.
MAX_LIQUIDITY_DROP_PCT = -20.0
LIVE_RECHECK_MIN_RATIO = 0.80
MAX_MESSAGE_CANDIDATES = 4
REQUEST_TIMEOUT = 15

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
SOLANA_RPC_URL = os.getenv(
    "SOLANA_RPC_URL",
    ("https://mainnet.helius-rpc.com/?api-key=" + HELIUS_API_KEY)
    if HELIUS_API_KEY else "https://api.mainnet-beta.solana.com",
)

def now_ts():
    return int(time.time())

def utc_iso(ts=None):
    return datetime.fromtimestamp(ts or now_ts(), timezone.utc).isoformat()

def num(v):
    try:
        x=float(v or 0)
        return x if math.isfinite(x) else 0.0
    except Exception:
        return 0.0

def money(v):
    x=num(v)
    if x>=1_000_000: return f"${x/1_000_000:.2f}M"
    if x>=1_000: return f"${x/1_000:.1f}K"
    return f"${x:.0f}"

def age_min(created):
    if not created:
        return None
    try:
        dt=datetime.fromisoformat(str(created).replace("Z","+00:00"))
        return max(0.0,(datetime.now(timezone.utc)-dt).total_seconds()/60)
    except Exception:
        return None

def get_json(url, params=None):
    r=requests.get(
        url, params=params,
        headers={"accept":"application/json;version=20230203"},
        timeout=REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()

def rpc(method, params):
    try:
        r=requests.post(
            SOLANA_RPC_URL,
            json={"jsonrpc":"2.0","id":"new-launch","method":method,"params":params},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        x=r.json()
        if x.get("error"):
            return None
        return x.get("result")
    except Exception:
        return None

def init_db(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS runs(
      run_id TEXT PRIMARY KEY,
      scan_ts INTEGER NOT NULL,
      status TEXT NOT NULL,
      networks_ok INTEGER NOT NULL,
      pools_seen INTEGER NOT NULL,
      observations INTEGER NOT NULL,
      candidates INTEGER NOT NULL,
      error_json TEXT NOT NULL DEFAULT '[]'
    );
    CREATE TABLE IF NOT EXISTS observations(
      run_id TEXT NOT NULL,
      scan_ts INTEGER NOT NULL,
      network TEXT NOT NULL,
      contract TEXT NOT NULL,
      symbol TEXT,
      name TEXT,
      pool_address TEXT,
      created_at TEXT,
      age_min REAL,
      price REAL,
      liquidity REAL,
      volume_5m REAL,
      volume_1h REAL,
      volume_24h REAL,
      change_5m REAL,
      change_1h REAL,
      change_24h REAL,
      buys_5m REAL,
      sells_5m REAL,
      tx_5m REAL,
      buy_sell_ratio_5m REAL,
      retention_pct REAL,
      liquidity_growth_pct REAL,
      price_growth_pct REAL,
      security_label TEXT,
      security_reason TEXT,
      holder_count INTEGER,
      top10_pct REAL,
      late_risk TEXT,
      score INTEGER,
      classification TEXT,
      reason_json TEXT,
      PRIMARY KEY(run_id,network,contract)
    );
    CREATE INDEX IF NOT EXISTS idx_newlaunch_asset
      ON observations(network,contract,scan_ts);
    CREATE TABLE IF NOT EXISTS signals(
      network TEXT NOT NULL,
      contract TEXT NOT NULL,
      first_signal_ts INTEGER NOT NULL,
      last_signal_ts INTEGER NOT NULL,
      first_signal_price REAL,
      last_signal_price REAL,
      highest_score INTEGER NOT NULL,
      signal_count INTEGER NOT NULL,
      PRIMARY KEY(network,contract)
    );
    CREATE TABLE IF NOT EXISTS signal_outcomes(
      network TEXT NOT NULL,
      contract TEXT NOT NULL,
      signal_ts INTEGER NOT NULL,
      checked_ts INTEGER NOT NULL,
      horizon_min INTEGER NOT NULL,
      price REAL,
      liquidity REAL,
      return_pct REAL,
      mfe_pct REAL,
      mae_pct REAL,
      PRIMARY KEY(network,contract,signal_ts,checked_ts)
    );
    CREATE TABLE IF NOT EXISTS signal_evaluations(
      network TEXT NOT NULL,
      contract TEXT NOT NULL,
      signal_ts INTEGER NOT NULL,
      checked_ts INTEGER NOT NULL,
      symbol TEXT,
      signal_price REAL,
      signal_liquidity REAL,
      current_price REAL,
      current_liquidity REAL,
      return_pct REAL,
      liquidity_change_pct REAL,
      outcome_class TEXT NOT NULL,
      outcome_reason TEXT NOT NULL,
      PRIMARY KEY(network,contract,signal_ts,checked_ts)
    );
    """)

def token_map(payload):
    out={}
    for obj in payload.get("included") or []:
        if obj.get("type")!="token": continue
        a=obj.get("attributes") or {}
        out[obj.get("id")]={
            "address":str(a.get("address") or ""),
            "symbol":str(a.get("symbol") or ""),
            "name":str(a.get("name") or ""),
        }
    return out

def rel_id(pool, name):
    try:
        return pool["relationships"][name]["data"]["id"]
    except Exception:
        return None

def parse_new_pools(network, payload):
    tmap=token_map(payload)
    out=[]
    for p in payload.get("data") or []:
        a=p.get("attributes") or {}
        base=tmap.get(rel_id(p,"base_token")) or {}
        quote=tmap.get(rel_id(p,"quote_token")) or {}
        symbol=base.get("symbol","").upper()
        if not base.get("address") or not symbol or symbol in STABLES:
            continue
        age=age_min(a.get("pool_created_at"))
        if age is None or age>MAX_WATCH_AGE_MIN:
            continue
        vol=a.get("volume_usd") or {}
        ch=a.get("price_change_percentage") or {}
        tx=a.get("transactions") or {}
        m5=tx.get("m5") or {}
        buys=num(m5.get("buys")); sells=num(m5.get("sells"))
        out.append({
            "network":network,
            "contract":base["address"],
            "symbol":base.get("symbol") or base["address"][:7],
            "name":base.get("name") or "",
            "quote_symbol":quote.get("symbol") or "",
            "pool_address":a.get("address") or "",
            "created_at":a.get("pool_created_at"),
            "age_min":age,
            "price":num(a.get("base_token_price_usd")),
            "liquidity":num(a.get("reserve_in_usd")),
            "volume_5m":num(vol.get("m5")),
            "volume_1h":num(vol.get("h1")),
            "volume_24h":num(vol.get("h24")),
            "change_5m":num(ch.get("m5")),
            "change_1h":num(ch.get("h1")),
            "change_24h":num(ch.get("h24")),
            "buys_5m":buys,
            "sells_5m":sells,
            "tx_5m":buys+sells,
            "buy_sell_ratio_5m":buys/max(sells,1.0) if buys+sells else 0.0,
        })
    return out

def sol_security(contract):
    result={
        "label":"UNKNOWN","reason":"Solana güvenlik verisi tamamlanamadı",
        "holder_count":None,"top10_pct":None,"hard_veto":False,
    }
    acc=rpc("getAccountInfo",[contract,{"encoding":"jsonParsed","commitment":"confirmed"}])
    if not acc or not acc.get("value"):
        return result
    value=acc["value"]; data=value.get("data") or {}
    info={}
    if isinstance(data,dict):
        info=(data.get("parsed") or {}).get("info") or {}
    mint_active=info.get("mintAuthority") is not None
    freeze_active=info.get("freezeAuthority") is not None
    program=str(value.get("owner") or "")
    warnings=[]
    if mint_active: warnings.append("mint yetkisi aktif")
    if freeze_active: warnings.append("freeze yetkisi aktif")
    if "TokenzQd" in program: warnings.append("Token-2022")

    largest=rpc("getTokenLargestAccounts",[contract,{"commitment":"confirmed"}])
    supply=num(info.get("supply"))
    if largest and supply>0:
        vals=(largest.get("value") or [])[:10]
        top=sum(num(x.get("amount")) for x in vals)
        result["top10_pct"]=100*top/supply if supply else None

    # Mint/freeze are material launch risks. Top10 is warning-only because
    # pool/system accounts can distort raw concentration.
    if freeze_active:
        result.update(label="WEAK", reason="freeze yetkisi aktif", hard_veto=True)
    elif mint_active:
        result.update(label="MEDIUM", reason="mint yetkisi aktif", hard_veto=False)
    else:
        result.update(label="STRONG", reason="mint/freeze kapalı" + (f"; {', '.join(warnings)}" if warnings else ""), hard_veto=False)
    return result

def evm_security(network, contract):
    result={
        "label":"UNKNOWN","reason":"GoPlus güvenlik verisi alınamadı",
        "holder_count":None,"top10_pct":None,"hard_veto":False,
    }
    chain=EVM_CHAIN_IDS.get(network)
    if not chain:
        return result
    try:
        x=get_json(
            f"https://api.gopluslabs.io/api/v1/token_security/{chain}",
            {"contract_addresses":contract},
        )
        row=((x.get("result") or {}).get(contract.lower())
             or (x.get("result") or {}).get(contract) or {})
        if not row:
            return result
        def yes(k): return str(row.get(k) or "").strip()=="1"
        bad=[]
        for k,label in [
            ("is_honeypot","honeypot"),
            ("cannot_sell_all","tam satış engeli"),
            ("transfer_pausable","transfer durdurulabilir"),
            ("hidden_owner","gizli owner"),
        ]:
            if yes(k): bad.append(label)
        buy_tax=100*num(row.get("buy_tax")); sell_tax=100*num(row.get("sell_tax"))
        if sell_tax>=20: bad.append(f"satış vergisi %{sell_tax:.0f}")
        holders=row.get("holder_count")
        try: result["holder_count"]=int(holders) if holders is not None else None
        except Exception: pass
        if bad:
            result.update(label="WEAK",reason=", ".join(bad),hard_veto=True)
        elif result["holder_count"] == 0:
            result.update(label="MEDIUM",reason="holder verisi henüz oluşmamış / sağlayıcı gecikmeli",hard_veto=False)
        elif sell_tax>=10 or buy_tax>=10 or yes("is_proxy"):
            result.update(label="MEDIUM",reason=f"vergi/proxy uyarısı (buy %{buy_tax:.1f}, sell %{sell_tax:.1f})",hard_veto=False)
        else:
            result.update(label="STRONG",reason=f"kritik GoPlus veto yok (buy %{buy_tax:.1f}, sell %{sell_tax:.1f})",hard_veto=False)
        return result
    except Exception:
        return result

def security(network, contract):
    return sol_security(contract) if network=="solana" else evm_security(network,contract)

def previous(c, network, contract):
    return c.execute(
        """SELECT * FROM observations
           WHERE network=? AND contract=?
           ORDER BY scan_ts DESC LIMIT 1""",(network,contract)
    ).fetchone()

def history_peak(c, network, contract):
    r=c.execute(
        """SELECT MAX(price) p FROM observations
           WHERE network=? AND contract=? AND price>0""",(network,contract)
    ).fetchone()
    return num(r["p"]) if r else 0.0

def classify(c, row, sec):
    reasons=[]; warnings=[]; score=0
    prev=previous(c,row["network"],row["contract"])
    peak=history_peak(c,row["network"],row["contract"])
    price=row["price"]; liq=row["liquidity"]
    retention=100.0 if peak<=0 or price>=peak else 100*price/peak
    liq_growth=None; price_growth=None
    if prev:
        pl=num(prev["liquidity"]); pp=num(prev["price"])
        if pl>0: liq_growth=100*(liq/pl-1)
        if pp>0 and price>0: price_growth=100*(price/pp-1)

    age=row["age_min"]
    if age>=MIN_AGE_MIN and age<=MAX_CANDIDATE_AGE_MIN:
        score+=1; reasons.append("yaş erken pencere içinde")
    elif age<MIN_AGE_MIN:
        warnings.append("çok yeni; davranış henüz oluşmadı")
    else:
        warnings.append("aday penceresinden yaşlı")

    if liq>=MIN_LIQUIDITY:
        score+=2; reasons.append(f"likidite {money(liq)}")
    else:
        warnings.append(f"likidite düşük {money(liq)}")

    if row["volume_5m"]>=MIN_VOLUME_5M:
        score+=1; reasons.append("5dk hacim aktif")
    else:
        warnings.append("5dk hacim zayıf")

    if row["tx_5m"]>=MIN_TX_5M:
        score+=1; reasons.append(f"5dk {int(row['tx_5m'])} işlem")
    else:
        warnings.append("5dk işlem sayısı yetersiz")

    if row["buy_sell_ratio_5m"]>=MIN_BUY_SELL_5M:
        score+=2; reasons.append(f"alıcı/satıcı {row['buy_sell_ratio_5m']:.1f}x")
    elif row["buy_sell_ratio_5m"]<0.8 and row["tx_5m"]>0:
        warnings.append("satıcı baskısı")

    if retention>=MIN_RETENTION:
        score+=1; reasons.append(f"retention %{retention:.0f}")
    else:
        warnings.append(f"ilk tepe korunmuyor (%{retention:.0f})")

    if liq_growth is not None:
        if liq_growth>=10:
            score+=1; reasons.append(f"likidite %{liq_growth:+.0f} büyüdü")
        elif liq_growth<=MAX_LIQUIDITY_DROP_PCT:
            score-=6; warnings.append(f"LIKIDITE COKUSU %{liq_growth:+.0f}")
    else:
        # First sighting is useful discovery, but one liquidity snapshot is not
        # evidence that liquidity will persist.
        warnings.append("likidite tek ölçüm; kalıcılık henüz doğrulanmadı")

    if sec["label"]=="STRONG":
        score+=2; reasons.append("güvenlik güçlü")
    elif sec["label"]=="MEDIUM":
        score+=0; warnings.append(sec["reason"])
    else:
        score-=4; warnings.append(sec["reason"])

    change=max(row["change_24h"],row["change_1h"])
    late="LOW"
    if change>=LATE_CHANGE:
        late="HIGH"; score-=3; warnings.append(f"hareket zaten %{change:.0f}")
    elif change>MAX_CANDIDATE_CHANGE:
        late="MEDIUM"; score-=1; warnings.append(f"hareket ilerlemiş %{change:.0f}")

    # Turnover can reveal thin-liquidity churn. Warning, not a single hard veto.
    turnover=row["volume_24h"]/max(liq,1.0)
    if turnover>20:
        score-=2; warnings.append(f"hacim/likidite aşırı {turnover:.1f}x")

    hard=(
        liq<MIN_LIQUIDITY
        or sec["hard_veto"]
        or (liq_growth is not None and liq_growth<=MAX_LIQUIDITY_DROP_PCT)
    )
    core_activity=(
        row["volume_5m"]>=MIN_VOLUME_5M
        and row["tx_5m"]>=MIN_TX_5M
        and row["buy_sell_ratio_5m"]>=MIN_BUY_SELL_5M
    )
    candidate_ready=(
        score>=MIN_SCORE_CANDIDATE
        and late=="LOW"
        and retention>=MIN_RETENTION
        and core_activity
        and sec["label"]=="STRONG"
        and age>=MIN_AGE_MIN
        and age<=MAX_CANDIDATE_AGE_MIN
    )
    if hard:
        cls="REJECT"
    elif candidate_ready:
        cls="CANDIDATE"
    elif age<=MAX_WATCH_AGE_MIN:
        cls="WATCH"
    else:
        cls="REJECT"

    return {
        "score":score,"classification":cls,"retention_pct":retention,
        "liquidity_growth_pct":liq_growth,"price_growth_pct":price_growth,
        "late_risk":late,"reasons":reasons,"warnings":warnings,
    }

def record(c, run_id, ts, row, sec, d):
    c.execute(
        """INSERT OR REPLACE INTO observations(
        run_id,scan_ts,network,contract,symbol,name,pool_address,created_at,age_min,
        price,liquidity,volume_5m,volume_1h,volume_24h,change_5m,change_1h,change_24h,
        buys_5m,sells_5m,tx_5m,buy_sell_ratio_5m,retention_pct,liquidity_growth_pct,
        price_growth_pct,security_label,security_reason,holder_count,top10_pct,
        late_risk,score,classification,reason_json)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            run_id,ts,row["network"],row["contract"],row["symbol"],row["name"],
            row["pool_address"],row["created_at"],row["age_min"],row["price"],
            row["liquidity"],row["volume_5m"],row["volume_1h"],row["volume_24h"],
            row["change_5m"],row["change_1h"],row["change_24h"],row["buys_5m"],
            row["sells_5m"],row["tx_5m"],row["buy_sell_ratio_5m"],
            d["retention_pct"],d["liquidity_growth_pct"],d["price_growth_pct"],
            sec["label"],sec["reason"],sec["holder_count"],sec["top10_pct"],
            d["late_risk"],d["score"],d["classification"],
            json.dumps({"support":d["reasons"],"warnings":d["warnings"]},ensure_ascii=False),
        )
    )
    if d["classification"]=="CANDIDATE":
        old=c.execute("SELECT * FROM signals WHERE network=? AND contract=?",
                      (row["network"],row["contract"])).fetchone()
        if old:
            c.execute("""UPDATE signals SET last_signal_ts=?,last_signal_price=?,
                      highest_score=MAX(highest_score,?),signal_count=signal_count+1
                      WHERE network=? AND contract=?""",
                      (ts,row["price"],d["score"],row["network"],row["contract"]))
        else:
            c.execute("""INSERT INTO signals VALUES(?,?,?,?,?,?,?,?)""",
                      (row["network"],row["contract"],ts,ts,row["price"],row["price"],d["score"],1))

def refresh_token_pool(network, contract):
    try:
        payload=get_json(
            f"{GECKO}/networks/{network}/tokens/{contract}/pools",
            {"include":"base_token,quote_token","page":1},
        )
        rows=parse_new_pools(network,payload)
        exact=[r for r in rows if (
            r["contract"]==contract if network=="solana"
            else r["contract"].lower()==contract.lower()
        )]
        if not exact:
            return None
        return max(exact,key=lambda r:r["liquidity"])
    except Exception:
        return None


def update_signal_outcomes(c, ts, limit=20):
    signals=c.execute(
        """SELECT * FROM signals
           WHERE first_signal_ts>=?
           ORDER BY last_signal_ts DESC LIMIT ?""",
        (ts-24*3600,limit)
    ).fetchall()
    updated=0
    for s in signals:
        live=refresh_token_pool(s["network"],s["contract"])
        if not live or num(s["first_signal_price"])<=0:
            continue

        # Recover the immutable signal-time snapshot for evaluation only.
        sig=c.execute(
            """SELECT symbol,price,liquidity FROM observations
               WHERE network=? AND contract=? AND scan_ts=?
               ORDER BY rowid ASC LIMIT 1""",
            (s["network"],s["contract"],s["first_signal_ts"])
        ).fetchone()
        signal_liq=num(sig["liquidity"]) if sig else 0.0
        signal_price=num(s["first_signal_price"])
        current_price=num(live.get("price"))
        current_liq=num(live.get("liquidity"))
        if current_price<=0:
            continue

        ret=100*(current_price/signal_price-1)
        old=c.execute(
            """SELECT MAX(return_pct) mx, MIN(return_pct) mn
               FROM signal_outcomes
               WHERE network=? AND contract=? AND signal_ts=?""",
            (s["network"],s["contract"],s["first_signal_ts"])
        ).fetchone()
        prior_mx=num(old["mx"]) if old and old["mx"] is not None else ret
        prior_mn=num(old["mn"]) if old and old["mn"] is not None else ret
        mfe=max(ret,prior_mx); mae=min(ret,prior_mn)
        horizon=max(0,int((ts-int(s["first_signal_ts"]))/60))
        c.execute(
            """INSERT OR REPLACE INTO signal_outcomes
               (network,contract,signal_ts,checked_ts,horizon_min,price,liquidity,
                return_pct,mfe_pct,mae_pct)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (s["network"],s["contract"],s["first_signal_ts"],ts,horizon,
             current_price,current_liq,ret,mfe,mae)
        )

        liq_change=(100*(current_liq/signal_liq-1)) if signal_liq>0 else None
        # Evaluation-only labels. They never change candidate/security thresholds.
        if signal_liq>0 and current_liq<=max(100.0,0.05*signal_liq):
            outcome="FALSE_POSITIVE_LIQUIDITY_COLLAPSE"
            reason=(
                f"Likidite sinyal anındaki ${signal_liq:.2f} seviyesinden "
                f"${current_liq:.2f} seviyesine çöktü"
            )
        elif ret<=-80:
            outcome="FALSE_POSITIVE_PRICE_COLLAPSE"
            reason=f"Fiyat sinyalden sonra %{ret:.1f} düştü"
        elif mfe>=15:
            outcome="FOLLOW_THROUGH_15_PLUS"
            reason=f"Sinyal sonrası MFE en az %{mfe:.1f}"
        elif mfe>=10:
            outcome="FOLLOW_THROUGH_10_PLUS"
            reason=f"Sinyal sonrası MFE en az %{mfe:.1f}"
        elif mfe>=5:
            outcome="FOLLOW_THROUGH_5_PLUS"
            reason=f"Sinyal sonrası MFE en az %{mfe:.1f}"
        else:
            outcome="OPEN_OR_WEAK_FOLLOW_THROUGH"
            reason=f"Şimdilik MFE %{mfe:.1f}, MAE %{mae:.1f}"

        c.execute(
            """INSERT OR REPLACE INTO signal_evaluations(
               network,contract,signal_ts,checked_ts,symbol,signal_price,
               signal_liquidity,current_price,current_liquidity,return_pct,
               liquidity_change_pct,outcome_class,outcome_reason)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (s["network"],s["contract"],s["first_signal_ts"],ts,
             sig["symbol"] if sig else None,signal_price,signal_liq,
             current_price,current_liq,ret,liq_change,outcome,reason)
        )
        updated+=1
        time.sleep(0.12)
    return updated

def scan():
    os.makedirs(os.path.dirname(DB) or ".",exist_ok=True)
    ts=now_ts(); run_id=f"NL-{ts}"
    errors=[]; all_rows=[]; networks_ok=0
    for network in NETWORKS:
        try:
            payload=get_json(
                f"{GECKO}/networks/{network}/new_pools",
                {"include":"base_token,quote_token","page":1},
            )
            rows=parse_new_pools(network,payload)
            all_rows.extend(rows); networks_ok+=1
        except Exception as e:
            errors.append(f"{network}:{type(e).__name__}")
        time.sleep(0.25)

    # Deduplicate token across multiple new pools; keep deepest current pool.
    best={}
    for r in all_rows:
        key=(r["network"],r["contract"].lower() if r["network"]!="solana" else r["contract"])
        if key not in best or r["liquidity"]>best[key]["liquidity"]:
            best[key]=r
    rows=list(best.values())
    rows.sort(key=lambda x:(x["age_min"],-x["liquidity"]))

    # Cap expensive security calls; keep earliest tradable/active launches.
    inspect=[
        r for r in rows
        if r["age_min"]>=MIN_AGE_MIN
        and (r["liquidity"]>=5_000 or r["volume_5m"]>=MIN_VOLUME_5M)
    ][:35]

    candidates=[]
    with sqlite3.connect(DB,timeout=30) as c:
        c.row_factory=sqlite3.Row; init_db(c)
        for r in inspect:
            sec=security(r["network"],r["contract"])
            d=classify(c,r,sec)

            # Candidate snapshots can become stale during the same scan on very
            # new pools. Re-read the deepest pool immediately before an alert.
            # If liquidity vanished or materially shrank, fail closed.
            if d["classification"]=="CANDIDATE":
                live=refresh_token_pool(r["network"],r["contract"])
                if live:
                    live_liq=num(live.get("liquidity"))
                    scan_liq=max(num(r.get("liquidity")),1.0)
                    live_ratio=live_liq/scan_liq
                    if live_liq<MIN_LIQUIDITY or live_ratio<LIVE_RECHECK_MIN_RATIO:
                        d["classification"]="REJECT"
                        d["score"]-=6
                        d["warnings"].insert(
                            0,
                            f"canlı tekrar kontrolde likidite düştü: "
                            f"{money(scan_liq)} → {money(live_liq)}"
                        )
                        # Record the freshest market state so the collapse is
                        # preserved in the observation history.
                        r=live
                    else:
                        # Use the freshest price/liquidity in the recorded alert.
                        r=live
                        d=classify(c,r,sec)

            record(c,run_id,ts,r,sec,d)
            if d["classification"]=="CANDIDATE":
                candidates.append((r,sec,d))
            time.sleep(0.12)
        c.execute("""INSERT INTO runs VALUES(?,?,?,?,?,?,?,?)""",
                  (run_id,ts,"VALID" if networks_ok>=3 else "DEGRADED",
                   networks_ok,len(all_rows),len(inspect),len(candidates),
                   json.dumps(errors,ensure_ascii=False)))
        # Existing signals are independently followed for up to 24h even after
        # they leave the "new pools" feed. This is evaluation only.
        update_signal_outcomes(c,ts)
        c.commit()
    candidates.sort(key=lambda x:(-x[2]["score"],x[0]["age_min"]))
    return run_id,ts,networks_ok,len(all_rows),len(inspect),candidates,errors

def format_message(result):
    run_id,ts,net_ok,pools,obs,cands,errors=result
    display_cands=[]
    with sqlite3.connect(DB,timeout=30) as c:
        c.row_factory=sqlite3.Row
        init_notification_state(c)
        for r,sec,d in cands:
            event=should_notify_candidate(c,ts,r,d)
            if event:
                display_cands.append((r,sec,d,event))
        c.commit()
    lines=[
        "🍼 NEW LAUNCH AVCI",
        "• Çok yeni tokenları tarar | 🔒 sadece gözlem",
        f"• {net_ok}/{len(NETWORKS)} ağ çalıştı | {pools} yeni havuz görüldü | {obs} detaylı incelendi",
        "",
    ]
    if not display_cands:
        lines += [
            "ℹ️ Yeni/değişen erken aday yok.",
            "Aynı durumda kalan tokenlar Telegram'da tekrar edilmedi; tarama ve kayıt devam ediyor.",
        ]
    else:
        for r,sec,d,notify_event in display_cands[:MAX_MESSAGE_CANDIDATES]:
            late={"LOW":"Düşük","MEDIUM":"Orta","HIGH":"Yüksek"}.get(d["late_risk"],"Bilinmiyor")
            why="; ".join(d["reasons"][:2]) if d["reasons"] else "erken aktivite güçleniyor"
            risk="; ".join(d["warnings"][:2]) if d["warnings"] else "anlık ek uyarı yok; likidite yine de değişebilir"
            lines.append(f"🟡 {notification_prefix(notify_event)} | {r['symbol']} [{r['network']}] — SADECE İZLE")
            lines.append(f"• Yaş: {r['age_min']:.0f} dk | hareket: 5dk %{r['change_5m']:+.1f} | 1s %{r['change_1h']:+.1f}")
            lines.append(f"• Neden: {why}")
            lines.append(f"• Risk: {risk}")
            lines.append(f"• Güvenlik: {sec['reason']}")
            lines.append(f"• Geç kalma: {late} | likidite: {money(r['liquidity'])}")
            lines.append("• Durum: alım adayı değil; holder/LP ve gerçek satış teyidi eksik.")
            lines.append(f"• Kontrat: {r['contract']}")
            lines.append("")
    if errors:
        lines.append("⚠️ Veri sorunu: " + ", ".join(errors[:3]))
    lines.append("Not: Bu motor sadece çok erken keşif yapar. Tüm güvenlik ve satış kontrolleri tamamlanmadan token alınabilir aday sayılmaz.")
    return "\n".join(lines)[:4000]

def send_telegram(text):
    token=os.getenv("TELEGRAM_BOT_TOKEN","").strip()
    chat=(os.getenv("NEW_LAUNCH_CHAT_ID","") or os.getenv("TELEGRAM_CHAT_ID","")).strip()
    if not token:
        print("Telegram token yok"); return False
    if not chat:
        try:
            x=requests.get(f"https://api.telegram.org/bot{token}/getUpdates",timeout=20).json()
            ids=[]
            for u in x.get("result") or []:
                m=u.get("message") or u.get("edited_message") or {}
                ch=m.get("chat") or {}
                if ch.get("type")=="private" and ch.get("id") is not None:
                    ids.append(str(ch["id"]))
            chat=ids[-1] if ids else ""
        except Exception:
            chat=""
    if not chat:
        print("Telegram chat id yok"); return False
    r=requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={"chat_id":chat,"text":text,"disable_web_page_preview":"true"},
        timeout=20,
    )
    r.raise_for_status()
    return True

if __name__=="__main__":
    result=scan()
    msg=format_message(result)
    print(msg)
    if os.getenv("NEW_LAUNCH_SEND_TELEGRAM","1")=="1":
        send_telegram(msg)
