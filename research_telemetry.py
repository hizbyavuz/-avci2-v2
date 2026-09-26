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

def start():
    return time.perf_counter(), utc_now()
