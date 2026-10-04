#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared anti-spam + compact Telegram formatting for Long/Short observers."""
from __future__ import annotations
import os, sqlite3, time

DB=os.getenv("LS_SIMPLE_NOTIFY_DB","long_short_simple_notify.db")
COOLDOWN_SECONDS=int(os.getenv("LS_SIMPLE_NOTIFY_COOLDOWN","3600"))
CORE_STABLE_BASES={x.strip().upper() for x in os.getenv("LS_CORE_STABLE_BASES","BTC,ETH,BNB,SOL,XRP,LINK,ADA,AVAX,LTC,BCH").split(",") if x.strip()}
FAST_FRESH_MIN_PCT=float(os.getenv("LS_FAST_FRESH_MIN_PCT","3.0"))
FAST_EXTENDED_MIN_PCT=float(os.getenv("LS_FAST_EXTENDED_MIN_PCT","10.0"))

def init_db():
    with sqlite3.connect(DB) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS sent_alerts(
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            level REAL NOT NULL,
            fingerprint TEXT NOT NULL,
            sent_at_epoch REAL NOT NULL,
            PRIMARY KEY(symbol,fingerprint)
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS ix_simple_alert_symbol_time ON sent_alerts(symbol,sent_at_epoch)")

def fingerprint(symbol,direction,level):
    # 4 significant decimals is enough to prevent repeat spam while allowing a genuinely new setup later.
    return f"{symbol}|{direction}|{float(level):.8g}"

def can_send(symbol,direction,level):
    init_db()
    fp=fingerprint(symbol,direction,level)
    now=time.time()
    with sqlite3.connect(DB) as con:
        if con.execute("SELECT 1 FROM sent_alerts WHERE symbol=? AND fingerprint=?",(symbol,fp)).fetchone():
            return False
        row=con.execute("SELECT MAX(sent_at_epoch) FROM sent_alerts WHERE symbol=?",(symbol,)).fetchone()
        if row and row[0] is not None and now-float(row[0])<COOLDOWN_SECONDS:
            return False
    return True

def mark_sent(symbol,direction,level):
    init_db()
    fp=fingerprint(symbol,direction,level)
    with sqlite3.connect(DB) as con:
        con.execute("INSERT OR REPLACE INTO sent_alerts(symbol,direction,level,fingerprint,sent_at_epoch) VALUES(?,?,?,?,?)",
                    (symbol,direction,float(level),fp,time.time()))

def classify_move(symbol, day_change_pct):
    """Fixed core/stable bucket + daily-move buckets for faster coins."""
    base=(symbol[:-4] if str(symbol).upper().endswith("USDT") else str(symbol)).upper()
    if base in CORE_STABLE_BASES:
        return "STABLE"
    move=abs(float(day_change_pct or 0.0))
    if move>=FAST_EXTENDED_MIN_PCT:
        return "FAST_EXTENDED"
    if move>=FAST_FRESH_MIN_PCT:
        return "FAST_FRESH"
    return "FAST_QUIET"

def format_alert(symbol,direction,level,move_class,day_change_pct=0.0):
    side="üstü" if direction=="LONG" else "altı"
    if move_class=="STABLE":
        arrow="🟦 GÜÇLÜ/STABİL"
        info="Oturmuş ve likit coin; ana seviye kırılımı izleniyor."
    elif move_class=="FAST_FRESH":
        arrow="⚡ HIZLI/TAZE"
        info=f"24s hareket %{float(day_change_pct or 0.0):+.1f}; hareket hızlanma bölgesinde."
    elif move_class=="FAST_EXTENDED":
        arrow="🔥 HIZLI/UZAMIŞ"
        info=f"24s hareket %{float(day_change_pct or 0.0):+.1f}; günlük hareket zaten büyük, kovalamaya dikkat."
    else:
        arrow="⚪ HIZLI/SAKİN"
        info=f"24s hareket %{float(day_change_pct or 0.0):+.1f}; henüz hızlı-hareket havuzuna girmedi."
    return (f"{arrow} | {symbol}\n"
            f"{float(level):,.8g} {side} mum kırarsa {direction} güçlenebilir.\n"
            f"Bilgi: {info}")
