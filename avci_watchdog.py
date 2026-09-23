"""Independent scheduled check for missing Avcı scans and stale research state."""

import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from avci_state_guard import artifacts, download, gh_json, valid_database
from binance_health_alert import telegram_call


LIMIT_MINUTES = 75  # Allows for queue delays and an occasionally skipped run.


def age_minutes(value, now):
    return (now - datetime.fromisoformat(value.replace("Z", "+00:00"))).total_seconds() / 60


def workflow_problem(runs, label, now, limit=LIMIT_MINUTES):
    successful = [run for run in runs if run["status"] == "completed"
                  and run["conclusion"] == "success"]
    if not successful:
        return f"{label}: basarili calisma bulunamadi"
    latest = max(successful, key=lambda run: run["updated_at"])
    minutes = age_minutes(latest["updated_at"], now)
    if minutes > limit:
        return f"{label}: son basarili tarama {minutes:.0f} dakika once"
    return None


def latest_binance_scan_problem(now):
    saved = artifacts("binance-avci2-state")
    if not saved:
        return "Binance: kayitli son veri bulunamadi"
    with tempfile.TemporaryDirectory() as folder:
        download(saved[0], folder)
        path = Path(folder) / "binance_avci2.db"
        if not valid_database(path, "scans"):
            return "Binance: son veritabani bozuk veya eksik"
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            row = conn.execute("""SELECT scan_time_utc, health_status FROM scans
                                  ORDER BY scan_time_utc DESC LIMIT 1""").fetchone()
        if not row:
            return "Binance: veritabaninda tarama kaydi yok"
        if age_minutes(row[0], now) > LIMIT_MINUTES:
            return f"Binance: son gercek tarama {age_minutes(row[0], now):.0f} dakika once"
        if row[1] == "INVALID":
            return "Binance: son taramanin verileri gecersiz"
    return None



def latest_gate_scan_problem(now):
    saved = artifacts("gate-avci2-signal-history")
    if not saved:
        return "Gate: kayitli son veri bulunamadi"
    with tempfile.TemporaryDirectory() as folder:
        download(saved[0], folder)
        path = Path(folder) / "avci2.db"
        if not valid_database(path, "gate_scan_health"):
            return "Gate: son veritabani bozuk veya scan-health eksik"
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            row = conn.execute("""SELECT scan_ts,status,source_errors
                FROM gate_scan_health ORDER BY scan_ts DESC LIMIT 1""").fetchone()
            spot = conn.execute("""SELECT scan_ts,status,error
                FROM gate_spot_health ORDER BY scan_ts DESC LIMIT 1""").fetchone() \
                if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='gate_spot_health'").fetchone() else None
        if not row:
            return "Gate: veritabaninda gercek tarama kaydi yok"
        age=(now-datetime.fromtimestamp(int(row[0]),timezone.utc)).total_seconds()/60
        if age>LIMIT_MINUTES:
            return f"Gate: son gercek tarama {age:.0f} dakika once"
        if row[1]!="VALID":
            return f"Gate: son on-chain veri sagligi {row[1]} ({row[2] or 'kaynak hatasi'})"
        if spot:
            spot_age=(now-datetime.fromtimestamp(int(spot[0]),timezone.utc)).total_seconds()/60
            if spot_age>LIMIT_MINUTES:
                return f"Gate Spot: son veri {spot_age:.0f} dakika once"
            if spot[1]!="VALID":
                return f"Gate Spot: veri sagligi {spot[1]} ({spot[2] or 'kaynak hatasi'})"
    return None


def send_warning(problems):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing; cannot alert")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not chat_id:
        updates = telegram_call(token, "getUpdates", {})
        private = [item["message"]["chat"]["id"] for item in updates.get("result", [])
                   if item.get("message", {}).get("chat", {}).get("type") == "private"]
        if not private:
            raise RuntimeError("TELEGRAM_CHAT_ID is missing and no private chat found")
        chat_id = private[-1]
    message = ("⚠️ AVCI TARAMA KONTROLU\n" + "\n".join(problems) +
               f"\nGitHub: https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions")
    result = telegram_call(token, "sendMessage", {"chat_id": chat_id, "text": message})
    if not result.get("ok"):
        raise RuntimeError("Telegram rejected watchdog warning")


def main():
    repo = os.environ["GITHUB_REPOSITORY"]
    now = datetime.now(timezone.utc)
    problems = []
    for workflow, label in (("binance_avci2.yml", "Binance"),
                            ("avci-v2.yml", "Gate on-chain"),
                            ("gate-spot-watch.yml", "Gate Spot")):
        response = gh_json(f"repos/{repo}/actions/workflows/{workflow}/runs?per_page=30")
        problem = workflow_problem(response["workflow_runs"], label, now)
        if problem:
            problems.append(problem)
    try:
        problem = latest_binance_scan_problem(now)
        if problem:
            problems.append(problem)
    except Exception as error:
        problems.append(f"Binance: kaydedilen tarama okunamadi ({type(error).__name__})")
    try:
        problem = latest_gate_scan_problem(now)
        if problem:
            problems.append(problem)
    except Exception as error:
        problems.append(f"Gate: kaydedilen tarama okunamadi ({type(error).__name__})")
    if problems:
        print("; ".join(problems))
        send_warning(problems)
    else:
        print("Binance, Gate on-chain ve Gate Spot son taramalari saglikli; "
              "Binance verisi okunabildi")


if __name__ == "__main__":
    main()
