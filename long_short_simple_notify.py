#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared anti-spam + compact Telegram formatting for Long/Short observers."""
from __future__ import annotations
import os, sqlite3, time

DB=os.getenv("LS_SIMPLE_NOTIFY_DB","long_short_simple_notify.db")
COOLDOWN_SECONDS=int(os.getenv("LS_SIMPLE_NOTIFY_COOLDOWN","3600"))

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

def classify_move(quote_volume_24h, short_range_pct):
    qv=float(quote_volume_24h or 0.0)
    rp=float(short_range_pct or 0.0)
    if qv>=150_000_000 and rp<=1.20:
        return "STABLE"
    return "FAST"

def format_alert(symbol,direction,level,move_class):
    side="üstü" if direction=="LONG" else "altı"
    arrow="🟦 STABİL/GÜÇLÜ" if move_class=="STABLE" else "⚡ HIZLI/OYNAK"
    info=("Daha likit ve daha sakin hareket ediyor."
          if move_class=="STABLE"
          else "Daha hızlı ve oynak; kırılım sonrası hareket sertleşebilir.")
    return (f"{arrow} | {symbol}\n"
            f"{float(level):,.8g} {side} mum kırarsa {direction} güçlenebilir.\n"
            f"Bilgi: {info}")
