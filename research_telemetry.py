#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared research telemetry for Avci engines.

Records request/observation timing only. It never changes candidate rules,
thresholds, scores, or order logic.
"""
import json
import os
import sqlite3
import time
from datetime import datetime, timezone

VERSION = "research-telemetry-v1-20260926"

def utc_now():
    return datetime.now(timezone.utc).isoformat()

def _db_path(source):
    return os.getenv("BINANCE_DB","binance_avci2.db") if source.upper()=="BINANCE" else os.getenv("AVCI_DB","avci2.db")

def init_db(source):
    db=_db_path(source)
    with sqlite3.connect(db,timeout=30) as c:
        c.execute("""CREATE TABLE IF NOT EXISTS research_source_latency(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          source_engine TEXT NOT NULL,
          provider TEXT NOT NULL,
          endpoint TEXT,
          request_started_utc TEXT NOT NULL,
          received_utc TEXT NOT NULL,
          latency_ms REAL,
          source_event_time_utc TEXT,
          age_at_receive_ms REAL,
          status TEXT NOT NULL,
          http_status INTEGER,
          error_text TEXT,
          context_json TEXT,
          version TEXT NOT NULL
        )""")
        c.execute("""CREATE INDEX IF NOT EXISTS idx_research_latency_engine_time
          ON research_source_latency(source_engine,received_utc)""")
        c.commit()

def record(source_engine, provider, endpoint, started_perf, started_utc,
           status="OK", http_status=None, error=None,
           source_event_time_utc=None, context=None):
    try:
        db=_db_path(source_engine)
        received=datetime.now(timezone.utc)
        latency_ms=max(0.0,(time.perf_counter()-float(started_perf))*1000.0)
        age=None
        if source_event_time_utc:
            try:
                ev=datetime.fromisoformat(str(source_event_time_utc).replace("Z","+00:00"))
                age=max(0.0,(received-ev).total_seconds()*1000.0)
            except Exception:
                age=None
        init_db(source_engine)
        with sqlite3.connect(db,timeout=30) as c:
            c.execute("""INSERT INTO research_source_latency
              (source_engine,provider,endpoint,request_started_utc,received_utc,
               latency_ms,source_event_time_utc,age_at_receive_ms,status,http_status,
               error_text,context_json,version)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (source_engine.upper(),provider,endpoint,started_utc,received.isoformat(),
               latency_ms,source_event_time_utc,age,status,http_status,
               (str(error)[:500] if error else None),
               json.dumps(context or {},ensure_ascii=False,sort_keys=True),VERSION))
            c.commit()
    except Exception:
        # Telemetry must never break the frozen scanner.
        pass

def _to_iso_timestamp(v):
    try:
        x=float(v)
        if x>1e12: x/=1000.0
        if x>1e9:
            return datetime.fromtimestamp(x,timezone.utc).isoformat()
    except Exception:
        pass
    if isinstance(v,str):
        try:
            return datetime.fromisoformat(v.replace("Z","+00:00")).astimezone(timezone.utc).isoformat()
        except Exception:
            return None
    return None

def infer_source_event_time(provider, endpoint, payload):
    """Conservative source-time inference. Returns None rather than guessing."""
    try:
        ep=str(endpoint or "").lower()
        p=str(provider or "").upper()
        if p.startswith("BINANCE") and "/klines" in ep and isinstance(payload,list) and payload:
            row=payload[-1]
            if isinstance(row,list) and len(row)>6:
                return _to_iso_timestamp(row[6])
        if p=="GECKOTERMINAL" and "ohlcv" in ep:
            rows=(((payload or {}).get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
            if rows and isinstance(rows[0],list):
                return _to_iso_timestamp(max(float(r[0]) for r in rows if isinstance(r,list) and r))
        if isinstance(payload,dict):
            for key in ("serverTime","timestamp","updated_at","last_updated_at","last_updated"):
                if key in payload:
                    out=_to_iso_timestamp(payload.get(key))
                    if out:return out
                attrs=((payload.get("data") or {}).get("attributes") or {}) if isinstance(payload.get("data"),dict) else {}
                if key in attrs:
                    out=_to_iso_timestamp(attrs.get(key))
                    if out:return out
    except Exception:
        return None
    return None

def start():
    return time.perf_counter(), utc_now()
