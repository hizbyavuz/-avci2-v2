"""Explain how each paper candidate moved during the 24h after its signal."""

import argparse
import csv
import json
import os
import sqlite3
import time
from datetime import date, datetime, time as clock_time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from avci_monthly_report import latest_backup
from avci_state_guard import download, valid_database


ISTANBUL = ZoneInfo("Europe/Istanbul")
HEADERS = {"accept": "application/json;version=20230203",
           "User-Agent": "avci-daily-followup/1.0"}
FIELDS = ("sistem", "sinyal_zamani_turkiye", "coin", "ag", "kontrat",
          "sinyal_fiyati_usd", "24s_fiyati_usd", "24s_degisimi_yuzde",
          "24s_hedef_zamani_turkiye", "olcum", "surum")


def utc_range(turkish_day):
    start = datetime.combine(turkish_day, clock_time(), ISTANBUL)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def signal_time_local(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(ISTANBUL)


def target_minute(signal_ms):
    return ((signal_ms + 24 * 3600 * 1000) // 60000) * 60000


def binance_price(symbol, target_ms, conn, session=requests):
    row = conn.execute("""SELECT close_price FROM raw_klines WHERE symbol=?
        AND interval_value='1m' AND open_time_ms=? LIMIT 1""",
                       (symbol, target_ms)).fetchone()
    if row and row[0] and row[0] > 0:
        return float(row[0])
    # A single closed minute containing the exact 24-hour time.
    for host in ("https://data-api.binance.vision", "https://api.binance.com"):
        try:
            response = session.get(host + "/api/v3/klines", params={
                "symbol": symbol, "interval": "1m", "startTime": target_ms,
                "endTime": target_ms + 59999, "limit": 1}, timeout=18)
            response.raise_for_status()
            rows = response.json()
            if rows and int(rows[0][0]) == target_ms:
                price = float(rows[0][4])
                return price if price > 0 else None
        except (requests.RequestException, KeyError, IndexError, ValueError, TypeError):
            continue
    return None


def gate_price(network, pool, contract, target_ms, session=requests):
    if not all((network, pool, contract)):
        return None
    target_sec = target_ms // 1000
    try:
        response = session.get(
            f"https://api.geckoterminal.com/api/v2/networks/{network}/pools/{pool}/ohlcv/minute",
            params={"aggregate": 1, "limit": 4, "currency": "usd",
                    "token": contract, "before_timestamp": target_sec + 120},
            headers=HEADERS, timeout=25)
        response.raise_for_status()
        rows = response.json().get("data", {}).get("attributes", {}).get("ohlcv_list", [])
        for row in rows:
            if int(row[0]) == target_sec:
                price = float(row[4])
                return price if price > 0 else None
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
        pass
    return None


def result_row(system, signal, symbol, network, contract, price, result,
               version, reason):
    stamp = signal.astimezone(ISTANBUL)
    target = stamp + timedelta(hours=24)
    return {"sistem": system, "sinyal_zamani_turkiye": stamp.strftime("%Y-%m-%d %H:%M"),
            "coin": symbol or "?", "ag": network or "", "kontrat": contract or "",
            "sinyal_fiyati_usd": price, "24s_fiyati_usd": result,
            "24s_degisimi_yuzde": round(100 * (result / price - 1), 2)
            if result is not None and price and price > 0 else None,
            "24s_hedef_zamani_turkiye": target.strftime("%Y-%m-%d %H:%M"),
            "olcum": "1 dakikalik kapanis" if result is not None else reason,
            "surum": version}


def binance_rows(path, start, end, session=requests, now=None):
    if not valid_database(path, "signal_events"):
        raise ValueError("Binance backup missing signal events")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        candidates = conn.execute("""SELECT signal_time_utc, symbol, signal_price,
            config_version FROM signal_events WHERE event_class='CANDIDATE'
            AND signal_time_utc>=? AND signal_time_utc<?
            ORDER BY signal_time_utc, id""", (start.isoformat(), end.isoformat())).fetchall()
        result = []
        now_ms = int((now or datetime.now(timezone.utc)).timestamp() * 1000)
        for stamp, symbol, price, version in candidates:
            signal = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            target_ms = target_minute(int(signal.timestamp() * 1000))
            elapsed = target_ms + 60000 <= now_ms
            measured = binance_price(symbol, target_ms, conn, session) if elapsed else None
            result.append(result_row("Binance", signal, symbol, "", "", price,
                                     measured, version, "fiyat verisi yok / coin kaldirilmis olabilir"
                                     if elapsed else "24 saat henuz dolmadi"))
        return result
    finally:
        conn.close()


def gate_rows(path, snapshot_path, start, end, session=requests, now=None):
    if not valid_database(path, "validation_events"):
        raise ValueError("Gate backup missing V5 validation events")
    names = {}
    if valid_database(snapshot_path, "snapshots"):
        with sqlite3.connect(f"file:{snapshot_path}?mode=ro", uri=True) as conn:
            for network, contract, symbol in conn.execute("""SELECT network_id,
                    token_contract, symbol FROM snapshots WHERE symbol IS NOT NULL
                    ORDER BY zaman_utc"""):
                names[(network.lower(), contract.lower())] = symbol
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        candidates = conn.execute("""SELECT signal_ts, network_id,
            token_contract, pool, signal_price, config_version
            FROM validation_events WHERE group_type='CANDIDATE'
            AND signal_ts>=? AND signal_ts<? ORDER BY signal_ts, id""",
            (int(start.timestamp()), int(end.timestamp()))).fetchall()
    result = []
    now_ms = int((now or datetime.now(timezone.utc)).timestamp() * 1000)
    for signal_ts, network, contract, pool, price, version in candidates:
        signal = datetime.fromtimestamp(signal_ts, tz=timezone.utc)
        target_ms = target_minute(signal_ts * 1000)
        elapsed = target_ms + 60000 <= now_ms
        measured = gate_price(network, pool, contract, target_ms, session) if elapsed else None
        result.append(result_row("Gate", signal,
                                 names.get((network.lower(), contract.lower()), "?"),
                                 network, contract, price, measured, version,
                                 "24s mum yok / havuz degismis olabilir"
                                 if elapsed else "24 saat henuz dolmadi"))
        if elapsed:
            time.sleep(2.2)  # Public GeckoTerminal request pacing.
    return result


def format_price(value):
    return f"${value:.8g}" if value is not None else "veri yok"


def build_message(day, rows, repo, run_id):
    available = [r for r in rows if r["24s_degisimi_yuzde"] is not None]
    lines = [f"AVCI - {day} ONERILERININ 24 SAAT SONRAKI DURUMU",
             "Saatler Turkiye saatidir. Ilk fiyat kaydedilen sinyal fiyatidir; "
             "ikinci fiyat tam 24 saatin doldugu 1 dakikalik mumun kapanisidir "
             "(en fazla 1 dakika fark). Gercek alim/satim degildir.", ""]
    if not rows:
        lines.append("Bu gun iki sistemde de yeni temiz aday kaydi yok.")
    for item in rows[:16]:
        move = (f"{item['24s_degisimi_yuzde']:+.2f}%" if item["24s_degisimi_yuzde"]
                is not None else "olculemedi")
        extra = f" ({item['ag']}, {item['kontrat'][:8]}...)" if item["ag"] else ""
        lines.append(f"{item['sistem']} {item['coin']}{extra} | {item['sinyal_zamani_turkiye']} "
                     f"{format_price(item['sinyal_fiyati_usd'])} → "
                     f"{format_price(item['24s_fiyati_usd'])} | {move}")
    if len(rows) > 16:
        lines.append(f"Diger {len(rows)-16} aday tabloda.")
    lines.extend(["", f"Toplam {len(rows)} aday; 24s fiyati olculen {len(available)}, "
                  f"olculemeyen {len(rows)-len(available)}.",
                  "Tum adaylarin tek tek tablosu:",
                  f"https://github.com/{repo}/actions/runs/{run_id}"])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--day", default="", help="Turkiye tarihi YYYY-MM-DD; default two days ago")
    parser.add_argument("--binance-db", default="")
    parser.add_argument("--gate-db", default="")
    parser.add_argument("--gate-snapshots", default="")
    args = parser.parse_args()
    day = date.fromisoformat(args.day) if args.day else (
        datetime.now(ISTANBUL).date() - timedelta(days=2))
    start, end = utc_range(day)
    output = Path(f"avci-gunluk-{day}")
    output.mkdir(exist_ok=True)
    if args.binance_db:
        binance_path = Path(args.binance_db)
    else:
        target = output / "binance"
        target.mkdir(exist_ok=True)
        download(latest_backup("binance-avci2-state", "binance"), target)
        binance_path = target / "binance_avci2.db"
    if args.gate_db:
        gate_path = Path(args.gate_db)
        gate_snapshots = Path(args.gate_snapshots) if args.gate_snapshots else gate_path.parent / "avci2.db"
    else:
        target = output / "gate"
        target.mkdir(exist_ok=True)
        download(latest_backup("gate-avci2-signal-history", "gate"), target)
        gate_path, gate_snapshots = target / "avci_validation_v5.db", target / "avci2.db"
    rows = binance_rows(binance_path, start, end) + gate_rows(
        gate_path, gate_snapshots, start, end)
    with (output / "tum_adaylar.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    message = build_message(day, rows,
                            os.environ.get("GITHUB_REPOSITORY", "hizbyavuz/-avci2-v2"),
                            os.environ.get("GITHUB_RUN_ID", ""))
    (output / "telegram.txt").write_text(message, encoding="utf-8")
    print(message)


if __name__ == "__main__":
    main()
