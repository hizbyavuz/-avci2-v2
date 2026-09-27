#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Safely mirror an existing SQLite state file to Turso Cloud.

Infrastructure-only:
- does not change signal thresholds, rulesets, scoring, or selection logic;
- local SQLite remains the primary database while shadow verification runs;
- missing Turso credentials are a clean no-op;
- sync failures must never corrupt or block the trading-research engines.

Usage:
  python turso_state_sync.py push <db_path> <url_env> <token_env>

Example:
  python turso_state_sync.py push binance_avci2.db \
      TURSO_BINANCE_DATABASE_URL TURSO_BINANCE_AUTH_TOKEN
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path


VERSION = "turso-shadow-sync-v1-20260928"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sqlite_summary(path: Path) -> dict:
    uri = f"file:{path.resolve()}?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=30)
    try:
        check = con.execute("PRAGMA quick_check").fetchone()
        status = str(check[0]) if check else "UNKNOWN"
        rows = con.execute(
            """SELECT name FROM sqlite_master
               WHERE type='table' AND name NOT LIKE 'sqlite_%'
               ORDER BY name"""
        ).fetchall()
        counts = {}
        for (name,) in rows:
            escaped = str(name).replace('"', '""')
            try:
                counts[str(name)] = int(
                    con.execute(f'SELECT COUNT(*) FROM "{escaped}"').fetchone()[0]
                )
            except sqlite3.Error:
                counts[str(name)] = None
        return {
            "quick_check": status,
            "table_count": len(rows),
            "row_counts": counts,
        }
    finally:
        con.close()


def push(path: Path, url_env: str, token_env: str) -> int:
    if not path.exists() or path.stat().st_size == 0:
        print(json.dumps({
            "version": VERSION,
            "status": "SKIPPED",
            "reason": "LOCAL_DB_MISSING",
            "db": str(path),
        }, ensure_ascii=False))
        return 0

    url = os.getenv(url_env, "").strip()
    token = os.getenv(token_env, "").strip()
    if not url or not token:
        print(json.dumps({
            "version": VERSION,
            "status": "SKIPPED",
            "reason": "TURSO_CREDENTIALS_NOT_CONFIGURED",
            "db": str(path),
            "url_env": url_env,
            "token_env": token_env,
        }, ensure_ascii=False))
        return 0

    before = sqlite_summary(path)
    if before["quick_check"].lower() != "ok":
        raise RuntimeError(
            f"Local SQLite quick_check failed before sync: {before['quick_check']}"
        )

    sha_before = sha256_file(path)

    try:
        import turso.sync
    except ImportError as exc:
        raise RuntimeError(
            "pyturso is not installed; install pyturso==0.7.2"
        ) from exc

    db = turso.sync.connect(
        str(path),
        remote_url=url,
        auth_token=token,
    )
    try:
        result = db.push()
    finally:
        db.close()

    after = sqlite_summary(path)
    if after["quick_check"].lower() != "ok":
        raise RuntimeError(
            f"Local SQLite quick_check failed after sync: {after['quick_check']}"
        )

    print(json.dumps({
        "version": VERSION,
        "status": "PUSH_OK",
        "db": str(path),
        "sha256_before": sha_before,
        "sha256_after": sha256_file(path),
        "table_count": after["table_count"],
        "row_counts": after["row_counts"],
        "push_result": str(result),
    }, ensure_ascii=False, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("push",))
    parser.add_argument("db_path")
    parser.add_argument("url_env")
    parser.add_argument("token_env")
    args = parser.parse_args()

    path = Path(args.db_path)
    if args.mode == "push":
        return push(path, args.url_env, args.token_env)
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({
            "version": VERSION,
            "status": "ERROR",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }, ensure_ascii=False), file=sys.stderr)
        raise
