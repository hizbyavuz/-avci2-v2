#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validation audit for Binance Avci 2.

This module does not change frozen signal thresholds or ranking logic.
It only checks whether the research pipeline is producing comparable,
timestamp-safe, auditable events.
"""

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

DB = os.getenv("BINANCE_DB", "binance_avci2.db")


def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def ensure_table(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS validation_audit_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            audited_at_utc TEXT NOT NULL,
            total_events INTEGER NOT NULL,
            candidate_events INTEGER NOT NULL,
            near_miss_events INTEGER NOT NULL,
            random_control_events INTEGER NOT NULL,
            spot_only_events INTEGER NOT NULL,
            full_data_events INTEGER NOT NULL,
            flow_unknown_before INTEGER NOT NULL,
            flow_unknown_after INTEGER NOT NULL,
            flow_orphan_unknown INTEGER NOT NULL,
            cooldown_violations INTEGER NOT NULL,
            entry_timing_violations INTEGER NOT NULL,
            unmatched_candidate_scans INTEGER NOT NULL,
            notes_json TEXT
        )"""
    )


def backfill_flow_classes(conn):
    before = conn.execute(
        "SELECT COUNT(*) FROM flow_observations WHERE event_class IS NULL OR event_class=''"
    ).fetchone()[0]

    # Safe, deterministic backfill: only copy the class already fixed on the
    # event with the exact same event_id. No post-outcome information is used.
    conn.execute(
        """UPDATE flow_observations
           SET event_class = (
               SELECT s.event_class
               FROM signal_events s
               WHERE s.event_id = flow_observations.event_id
           )
           WHERE (event_class IS NULL OR event_class='')
             AND EXISTS (
               SELECT 1 FROM signal_events s
               WHERE s.event_id = flow_observations.event_id
                 AND s.event_class IS NOT NULL
                 AND s.event_class <> ''
           )"""
    )

    after = conn.execute(
        "SELECT COUNT(*) FROM flow_observations WHERE event_class IS NULL OR event_class=''"
    ).fetchone()[0]

    orphan = conn.execute(
        """SELECT COUNT(*)
           FROM flow_observations f
           WHERE (f.event_class IS NULL OR f.event_class='')
             AND NOT EXISTS (
                 SELECT 1 FROM signal_events s WHERE s.event_id=f.event_id
             )"""
    ).fetchone()[0]
    return before, after, orphan


def cooldown_violations(conn):
    rows = conn.execute(
        """SELECT symbol,event_class,signal_time_utc,cooldown_hours
           FROM signal_events
           WHERE event_class IN ('CANDIDATE','NEAR_MISS','RANDOM_CONTROL')
           ORDER BY symbol,event_class,signal_time_utc"""
    ).fetchall()
    violations = 0
    prev = {}
    for row in rows:
        dt = parse_dt(row["signal_time_utc"])
        if dt is None:
            continue
        key = (row["symbol"], row["event_class"])
        last = prev.get(key)
        hours = int(row["cooldown_hours"] or 24)
        if last is not None and dt - last < timedelta(hours=hours):
            violations += 1
        prev[key] = dt
    return violations


def entry_timing_violations(conn):
    rows = conn.execute(
        """SELECT signal_time_utc,entry_time_utc,entry_delay_seconds,entry_status
           FROM signal_events
           WHERE entry_time_utc IS NOT NULL
             AND entry_status IS NOT NULL"""
    ).fetchall()
    violations = 0
    for row in rows:
        signal_dt = parse_dt(row["signal_time_utc"])
        entry_dt = parse_dt(row["entry_time_utc"])
        if signal_dt is None or entry_dt is None:
            continue
        expected = signal_dt + timedelta(seconds=int(row["entry_delay_seconds"] or 0))
        if entry_dt < expected:
            violations += 1
    return violations


def unmatched_candidate_scans(conn):
    # Controls should be contemporaneous with candidates. A scan with at least
    # one candidate but neither near-miss nor random control is flagged for
    # research-quality review, not treated as a failed signal.
    return conn.execute(
        """WITH grouped AS (
             SELECT signal_time_utc,
                    SUM(CASE WHEN event_class='CANDIDATE' THEN 1 ELSE 0 END) c,
                    SUM(CASE WHEN event_class='NEAR_MISS' THEN 1 ELSE 0 END) n,
                    SUM(CASE WHEN event_class='RANDOM_CONTROL' THEN 1 ELSE 0 END) r
             FROM signal_events
             GROUP BY signal_time_utc
           )
           SELECT COUNT(*) FROM grouped
           WHERE c > 0 AND (n = 0 OR r = 0)"""
    ).fetchone()[0]


def main():
    if not os.path.exists(DB):
        print("VALIDATION_AUDIT | DB yok")
        return

    with sqlite3.connect(DB) as conn:
        conn.row_factory = sqlite3.Row
        ensure_table(conn)

        before, after, orphan = backfill_flow_classes(conn)
        total = conn.execute("SELECT COUNT(*) FROM signal_events").fetchone()[0]

        counts = {
            row["event_class"]: row["n"]
            for row in conn.execute(
                """SELECT COALESCE(event_class,'UNKNOWN') event_class,COUNT(*) n
                   FROM signal_events GROUP BY COALESCE(event_class,'UNKNOWN')"""
            )
        }
        modes = {
            row["data_mode"]: row["n"]
            for row in conn.execute(
                """SELECT COALESCE(data_mode,'UNKNOWN') data_mode,COUNT(*) n
                   FROM signal_events GROUP BY COALESCE(data_mode,'UNKNOWN')"""
            )
        }

        cooldown_bad = cooldown_violations(conn)
        entry_bad = entry_timing_violations(conn)
        unmatched = unmatched_candidate_scans(conn)

        notes = {
            "frozen_thresholds_changed": False,
            "flow_class_backfill": "event_id exact-match only",
            "primary_analysis_rule": "SPOT_FUTURES_FULL should remain separate from SPOT_ONLY",
            "unknown_policy": "unresolved/orphan rows stay explicit; never silently drop",
            "cluster_policy": "same-scan and repeated-symbol events must be clustered in inference",
            "multiple_testing_policy": "use FDR for multi-target/model comparisons",
        }

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO validation_audit_runs (
                audited_at_utc,total_events,candidate_events,near_miss_events,
                random_control_events,spot_only_events,full_data_events,
                flow_unknown_before,flow_unknown_after,flow_orphan_unknown,
                cooldown_violations,entry_timing_violations,
                unmatched_candidate_scans,notes_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                now,
                total,
                counts.get("CANDIDATE", 0),
                counts.get("NEAR_MISS", 0),
                counts.get("RANDOM_CONTROL", 0),
                modes.get("SPOT_ONLY", 0),
                modes.get("SPOT_FUTURES_FULL", 0),
                before,
                after,
                orphan,
                cooldown_bad,
                entry_bad,
                unmatched,
                json.dumps(notes, ensure_ascii=False, sort_keys=True),
            ),
        )
        conn.commit()

        print("VALIDATION_AUDIT")
        print(f"events={total} candidate={counts.get('CANDIDATE',0)} "
              f"near={counts.get('NEAR_MISS',0)} random={counts.get('RANDOM_CONTROL',0)}")
        print(f"modes spot_only={modes.get('SPOT_ONLY',0)} "
              f"full={modes.get('SPOT_FUTURES_FULL',0)}")
        print(f"flow_unknown before={before} after={after} orphan={orphan}")
        print(f"cooldown_violations={cooldown_bad}")
        print(f"entry_timing_violations={entry_bad}")
        print(f"candidate_scans_missing_control={unmatched}")


if __name__ == "__main__":
    main()
