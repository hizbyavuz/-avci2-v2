"""Readable monthly paper-test report. No trading or rule changes."""

import argparse
import csv
import json
import os
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from avci_state_guard import artifacts, download, valid_database


BINANCE_FIELDS = ("zaman", "coin", "sinif", "surum", "dogrulama",
                  "durum", "sonuc_5", "sonuc_10", "net_yuzde", "btc_ustu_yuzde")
GATE_FIELDS = ("zaman", "coin", "ag", "kontrat", "sinif", "surum", "durum",
               "sonuc_5", "sonuc_10", "net_yuzde", "maliyet_durumu")


def month_window(month):
    try:
        start = datetime.strptime(month, "%Y-%m").replace(tzinfo=timezone.utc)
    except ValueError as error:
        raise ValueError("Ay YYYY-MM biciminde olmali") from error
    if start.strftime("%Y-%m") != month:
        raise ValueError("Ay YYYY-MM biciminde olmali")
    end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start.isoformat(), end.isoformat()


def barrier_result(raw, target):
    try:
        return json.loads(raw or "{}").get("72", {}).get(str(float(target)), {}).get("result")
    except (ValueError, TypeError, AttributeError):
        return None


def read_binance(path, month):
    if not valid_database(path, "signal_events"):
        raise ValueError("Binance veri dosyasi okunamiyor")
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        source = conn.execute("""SELECT e.signal_time_utc, e.symbol, e.event_class,
            e.config_version, e.validation_tier, e.outcome_status,
            o.label_status, o.barrier_results_json, o.net_return_pct,
            o.excess_vs_btc_pct FROM signal_events e
            LEFT JOIN outcome_labels o ON o.event_id=e.event_id
            WHERE substr(e.signal_time_utc, 1, 7)=?
              AND e.event_class IN ('CANDIDATE','NEAR_MISS','RANDOM_CONTROL')
            ORDER BY e.signal_time_utc, e.id""", (month,)).fetchall()
        invalid = conn.execute("""SELECT COUNT(*) FROM scans
            WHERE substr(scan_time_utc, 1, 7)=? AND health_status='INVALID'""",
                               (month,)).fetchone()[0]
        scans = conn.execute("""SELECT COUNT(*) FROM scans
            WHERE substr(scan_time_utc, 1, 7)=?""", (month,)).fetchone()[0]
    rows = [{"zaman": r["signal_time_utc"], "coin": r["symbol"],
             "sinif": r["event_class"], "surum": r["config_version"],
             "dogrulama": r["validation_tier"],
             "durum": r["label_status"] or r["outcome_status"],
             "sonuc_5": barrier_result(r["barrier_results_json"], 5),
             "sonuc_10": barrier_result(r["barrier_results_json"], 10),
             "net_yuzde": r["net_return_pct"],
             "btc_ustu_yuzde": r["excess_vs_btc_pct"]} for r in source]
    return rows, scans, invalid


def read_gate(path, month, snapshot_db=None):
    if not valid_database(path, "validation_events"):
        raise ValueError("Gate V5 deneme dosyasi okunamiyor")
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        source = conn.execute("""SELECT signal_iso, network_id, token_contract,
            group_type, config_version, status, result_5, result_10,
            net_final_pct, cost_status FROM validation_events
            WHERE substr(signal_iso, 1, 7)=? ORDER BY signal_ts, id""",
                              (month,)).fetchall()
    names = {}
    if snapshot_db and valid_database(snapshot_db, "snapshots"):
        with sqlite3.connect(f"file:{snapshot_db}?mode=ro", uri=True) as conn:
            for network, contract, symbol in conn.execute("""SELECT network_id,
                    token_contract, symbol FROM snapshots WHERE symbol IS NOT NULL
                    ORDER BY zaman_utc"""):
                names[(network.lower(), contract.lower())] = symbol
    return [{"zaman": r["signal_iso"],
             "coin": names.get((r["network_id"].lower(),
                               r["token_contract"].lower()), "bilinmiyor"),
             "ag": r["network_id"],
             "kontrat": r["token_contract"], "sinif": r["group_type"],
             "surum": r["config_version"], "durum": r["status"],
             "sonuc_5": r["result_5"], "sonuc_10": r["result_10"],
             "net_yuzde": r["net_final_pct"],
             "maliyet_durumu": r["cost_status"]} for r in source]


def summarize(rows, venue):
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["surum"], row["sinif"], row.get("dogrulama", "V5"))].append(row)
    result = []
    for (version, group, tier), items in sorted(grouped.items()):
        closed = [r for r in items if r["durum"] == ("CLOSED" if venue == "Binance"
                                                   else "CLOSED_72H")]
        values = [float(r["net_yuzde"]) for r in closed
                  if r["net_yuzde"] is not None]
        hits = sum(r["sonuc_5"] == ("TARGET" if venue == "Binance"
                                     else "TARGET_FIRST") for r in closed)
        result.append({"version": version, "group": group, "tier": tier,
                       "total": len(items), "closed": len(closed),
                       "hits": hits, "net_count": len(values),
                       "avg_net": statistics.fmean(values) if values else None})
    return result


def write_csv(path, fields, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def latest_backup(name, venue):
    saved = artifacts(name)
    if saved:
        return saved[0]
    for offset in range(31):
        day = (datetime.now(timezone.utc) - timedelta(days=offset)).date()
        saved = artifacts(f"{venue}-avci2-daily-{day}")
        if saved:
            return saved[0]
    raise RuntimeError(f"Aylik rapor icin yedek eksik: {name}")


def render(month, binance_rows, gate_rows, scans, invalid):
    lines = [f"# Avci {month} aylik deneme sonuclari", "",
             "Bu tablo gercek al-sat islemi degil, sinyal sonrasi kagit ustu takiptir.",
             "Acik/eksik kayitlar basari oraninin paydasina katilmaz.",
             "Farkli surumler ve Binance gozlemsel sinyalleri ayri tutulur.", "",
             f"Binance tarama: {scans}; gecersiz tarama: {invalid}.",
             f"Binance olay: {len(binance_rows)}; Gate V5 olay: {len(gate_rows)}.", "",
             "| Yer | Surum | Grup | Dogrulama | Toplam | Sonuclanan | +%5 once | Ortalama net % |",
             "|---|---|---|---|---:|---:|---:|---:|"]
    summaries = {"Binance": summarize(binance_rows, "Binance"),
                 "Gate": summarize(gate_rows, "Gate")}
    for venue, groups in summaries.items():
        for group in groups:
            net = (f"{group['avg_net']:+.2f} ({group['net_count']} kayit)"
                   if group["avg_net"] is not None else "olculemedi")
            lines.append(f"| {venue} | {group['version']} | {group['group']} | "
                         f"{group['tier']} | {group['total']} | {group['closed']} | "
                         f"{group['hits']}/{group['closed']} | {net} |")
    lines.extend(["", "Ayrintilar: Binance ve Gate CSV tablolarinda her aday ve "
                  "karsilastirma grubu tek tek bulunur.",
                  "Az sayida kapanmis ornek iyi bir sistemin kaniti degildir."])
    return "\n".join(lines) + "\n", summaries


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", default="")
    parser.add_argument("--binance-db", default="")
    parser.add_argument("--gate-db", default="")
    args = parser.parse_args()
    month = args.month or (datetime.now(timezone.utc).replace(day=1)
                           - timedelta(days=1)).strftime("%Y-%m")
    month_window(month)
    directory = Path(f"avci-aylik-{month}")
    directory.mkdir(exist_ok=True)
    binance_path = Path(args.binance_db) if args.binance_db else None
    gate_path = Path(args.gate_db) if args.gate_db else None
    if not binance_path or not gate_path:
        for name, filename, table in (("binance-avci2-state", "binance_avci2.db", "scans"),
                                      ("gate-avci2-signal-history", "avci_validation_v5.db",
                                       "validation_events")):
            saved = latest_backup(name, "binance" if name.startswith("binance")
                                  else "gate")
            target = directory / ("binance" if name.startswith("binance") else "gate")
            target.mkdir(exist_ok=True)
            download(saved, target)
            if not valid_database(target / filename, table):
                raise RuntimeError(f"Aylik rapor icin dosya eksik/bozuk: {filename}")
            if name.startswith("binance"):
                binance_path = target / filename
            else:
                gate_path = target / filename
    binance_rows, scans, invalid = read_binance(binance_path, month)
    gate_rows = read_gate(gate_path, month, gate_path.parent / "avci2.db")
    write_csv(directory / "binance_olaylar.csv", BINANCE_FIELDS, binance_rows)
    write_csv(directory / "gate_olaylar.csv", GATE_FIELDS, gate_rows)
    report, summaries = render(month, binance_rows, gate_rows, scans, invalid)
    (directory / "OZET.md").write_text(report, encoding="utf-8")
    candidates = [g for g in summaries["Binance"] if g["group"] == "CANDIDATE"]
    gate_candidates = [g for g in summaries["Gate"] if g["group"] == "CANDIDATE"]
    def short(groups):
        return "; ".join(f"{g['closed']}/{g['total']} sonuclandi, "
                         f"+%5: {g['hits']}/{g['closed']}, "
                         f"ort. net: {g['avg_net']:+.1f}% "
                         f"({g['version']})" if g["avg_net"] is not None
                         else f"{g['closed']}/{g['total']} sonuclandi, "
                              f"+%5: {g['hits']}/{g['closed']}, net olculemedi "
                              f"({g['version']})"
                         for g in groups) or "kayit yok"
    link = f"https://github.com/{os.environ.get('GITHUB_REPOSITORY', 'hizbyavuz/-avci2-v2')}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}"
    message = (f"AVCI {month} AYLIK DENEME OZETI\n"
               f"Binance: {short(candidates)}\nGate: {short(gate_candidates)}\n"
               f"Gecersiz Binance taramasi: {invalid}/{scans}.\n"
               "Bu sayilar gercek al-sat kazanci degildir. Acik kayitlar ayri tutuldu.\n"
               f"Tum coinler ve ayrintilar: {link} (Artifacts: avci-aylik-{month})")
    (directory / "telegram.txt").write_text(message, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
