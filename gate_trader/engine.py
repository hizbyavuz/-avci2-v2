"""Canlı motor: Gate tarama, RAM defteri, sonuç takibi. Otomatik emir YOK."""
from __future__ import annotations
import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from . import config as C
from . import gate_api as G
from . import ledger as LG
from . import messages as M
from . import telegram as TG
from . import early_outcomes as EO
from .gates import evaluate, trend
from .outcome import pnl_usd, position_notional, simulate
from .early_watch import classify as classify_early

# RAM resets on deploy; suppress repeated watch alerts briefly after restart.
_STARTED_AT = time.time()
_EARLY_RESTART_QUIET_SEC = 3600

def _eval_coin(row:dict, btc4h, now_ts:float):
    con=row["contract"]
    try:
        c4h=G.candles(con,"4h",120,now_ts) if con!=C.BTC else btc4h
        c1h=G.candles(con,"1h",300,now_ts)
        c15=G.candles(con,"15m",60,now_ts)
        if len(c15)<30 or len(c1h)<80:return con,None,"VERI"
        i=len(c15)-1
        if c15.t[i]+c15.sec<now_ts-2*c15.sec:return con,None,"VERI"
        d=evaluate(btc4h,c4h,c1h,c15,i,vol24=row["vol24"],funding=row.get("funding"))
        # Observational early-watch: no bypass of confirmation/risk gates.
        if not d.ok:
            direction=trend(c4h,c4h.last_closed_index(now_ts))
            side="LONG" if direction=="UP" else "SHORT" if direction=="DOWN" else None
            if side:
                candles=[{"high":c15.h[k],"low":c15.l[k],"close":c15.c[k],
                          "volume":c15.qv[k],"open":c15.o[k]} for k in range(len(c15))]
                d.early=classify_early(candles,side)
                d.early_ts=c15.t[i]+c15.sec
        return con,d,d.gate
    except Exception as exc:
        print("COIN_HATA",con,type(exc).__name__,str(exc)[:150],flush=True)
        return con,None,"HATA"

def scan(now_ts:float|None=None)->dict:
    now_ts=time.time() if now_ts is None else now_ts
    funnel=Counter()
    btc4h=G.candles(C.BTC,"4h",120,now_ts)
    market=trend(btc4h,btc4h.last_closed_index(now_ts))
    uni=G.universe()
    passed=[]
    early=[]
    with ThreadPoolExecutor(max_workers=C.WORKERS) as pool:
        for con,d,gate in pool.map(lambda r:_eval_coin(r,btc4h,now_ts),uni):
            funnel[gate]+=1
            if d is not None and d.ok:passed.append((con,d))
            elif d is not None and getattr(d,"early",None) is not None:
                early.append((con,d.early,d.early_ts))
    sent=0
    with LG.connect() as db:
        open_n=len(LG.open_signals(db))
        # Early alerts are watch-only, never paper-trade entries. One per coin/side/state/closed bar.
        for con,watch,bar_ts in early:
            if now_ts - _STARTED_AT < _EARLY_RESTART_QUIET_SEC:
                funnel["ERKEN_RESTART_SESSIZ"] += 1
                continue
            if watch["state"]!="ERKEN_IZLE" or LG.is_open(db,con):
                continue
            key=f"early:{con}:{watch['side']}"
            last=LG.get_meta(db,key)
            if last and bar_ts-int(last)<C.COOLDOWN_HOURS*3600:
                continue
            arrow="🟢" if watch["side"]=="LONG" else "🔴"
            verb="üstünde" if watch["side"]=="LONG" else "altında"
            coin_name=con.replace("_USDT","")
            direction=watch["side"]
            trigger=watch["trigger"]
            price=watch["price"]
            ratio=watch["volume_ratio"]
            travel=watch.get("travel_pct",0)
            msg=(f"{arrow} ERKEN {direction} İÇİN İZLE | {coin_name}\n"
                 f"🎯 İzleme seviyesi: {trigger:.8g}\n"
                 f"💵 Son kapanış: {price:.8g}\n"
                 f"📊 Hacim: normalin {ratio:.2f} katı\n"
                 f"📈 İlk hareketten bu yana: %{travel:+.2f}\n"
                 f"👀 Beklenen: 15 dk mum {trigger:.8g} {verb} kapansın, seviye korunsun.\n"
                 "🟡 Henüz işlem sinyali değil. Stop/hedef yok.")
            if C.SEND_SIGNALS and TG.send(msg):
                LG.set_meta(db,key,bar_ts)
                EO.record(db,con,watch,bar_ts)
                funnel["ERKEN_UYARI"]+=1
        for con,d in sorted(passed,key=lambda x:-(x[1].net_rr or 0)):
            if LG.is_open(db,con):
                funnel["ZATEN_ACIK"]+=1
                continue
            last=LG.last_signal_ts(db,con)
            if last and d.signal_ts-last<C.COOLDOWN_HOURS*3600:
                funnel["BEKLEME"]+=1
                continue
            if open_n>=C.MAX_OPEN:
                funnel["MAX_ACIK"]+=1
                continue
            try:
                oi=G.oi_change_1h(con)
            except Exception as exc:
                oi=None
                print("OI_HATA",con,type(exc).__name__,flush=True)
            if oi is not None:d.reasons.append(f"OI son 1s: %{oi:+.1f} (bilgi)")
            notional=position_notional(d.stop_pct)
            sid=LG.insert_signal(db,con,d,notional,C.ROUND_TRIP_COST_PCT)
            if sid is None:continue
            open_n+=1
            if C.SEND_SIGNALS and TG.send(M.signal(sid,con,d,notional)):
                db.execute("UPDATE signals SET sent=1 WHERE id=?",(sid,))
            if C.SEND_SIGNALS and db.execute("SELECT sent FROM signals WHERE id=?",(sid,)).fetchone()["sent"]:
                sent+=1
        db.execute("INSERT OR REPLACE INTO scans(ts,coins,funnel,errors,signals) VALUES(?,?,?,?,?)",
                   (int(now_ts),len(uni),json.dumps(funnel),funnel.get("HATA",0),sent))
    res={"market":market,"coins":len(uni),"funnel":dict(funnel),"signals":sent}
    print("TARAMA",json.dumps(res,ensure_ascii=False),flush=True)
    return res

def track(now_ts:float|None=None)->int:
    now_ts=time.time() if now_ts is None else now_ts
    closed=0
    with LG.connect() as db:
        for row in LG.open_signals(db):
            try:s=G.candles_since(row["contract"],"5m",int(row["signal_ts"]))
            except Exception as exc:
                print("TAKIP_HATA",row["contract"],exc,flush=True)
                continue
            keep=[k for k in range(len(s)) if s.t[k]>=row["signal_ts"]]
            t=[s.t[k] for k in keep];h=[s.h[k] for k in keep]
            l=[s.l[k] for k in keep];c=[s.c[k] for k in keep]
            timeout_ts=int(row["signal_ts"]+C.TIMEOUT_HOURS*3600)
            out=simulate(row["side"],row["sl"],row["tp"],t,h,l,c,300,timeout_ts)
            if out.status=="OPEN" and now_ts>=timeout_ts+300 and c:
                out.status,out.exit_price,out.exit_ts="TIMEOUT",c[-1],t[-1]+300
            if out.status=="OPEN":continue
            pnl=pnl_usd(row["side"],row["entry"],out.exit_price,row["stop_pct"],
                        row["risk_usd"],row["cost_pct"])
            LG.close_signal(db,row["id"],out.status,out.exit_price,out.exit_ts,pnl)
            closed+=1
            if row["sent"]:TG.send(M.outcome(row,out.status,out.exit_price,pnl,LG.stats(db)))
    return closed

def daily_summary(force:bool=False)->bool:
    now=datetime.now(timezone.utc)
    key=now.strftime("%Y-%m-%d")
    with LG.connect() as db:
        if not force and (now.hour<C.DAILY_SUMMARY_UTC_HOUR or LG.get_meta(db,"summary_day")==key):
            return False
        day_ago=int(time.time())-86400
        today=LG.stats(db,since_ts=day_ago)
        total=LG.stats(db)
        scans=db.execute("SELECT COUNT(*) n FROM scans WHERE ts>=?",(day_ago,)).fetchone()["n"]
        funnel=Counter()
        for r in db.execute("SELECT funnel FROM scans WHERE ts>=?",(day_ago,)):
            funnel.update(json.loads(r["funnel"] or "{}"))
        message=M.summary("Son 24 saat",today,scans)+"\n\n"+M.summary("Tüm defter",total)+"\n\n"+EO.summary(db)
        if funnel:
            names={"1-PIYASA":"BTC yönsüz","2-COIN":"trend uymadı","3-SEVIYE":"seviyede değil",
                   "4-TEYIT":"teyit yok","5-RISK":"risk/ödül kötü","TAMAM":"sinyal"}
            parts=[f"{names.get(k,k)} {v}" for k,v in funnel.most_common() if k in names]
            message+="\n\nEleme (24s): "+", ".join(parts)
        delivered=TG.send(message)
        if delivered:LG.set_meta(db,"summary_day",key)
    return delivered
