#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import math
import sqlite3
from pathlib import Path

DB_FILE = "binance_avci2.db"
REPORT_FILE = "binance_latest_report.md"


def fmt(value, digits=2):
    if value is None:
        return "-"
    return f"{value:.{digits}f}"


def wilson_interval(successes, total, z=1.96):
    if not total:
        return None, None
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return 100 * (center - margin), 100 * (center + margin)


def main():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row

    latest = conn.execute(
        """
        SELECT config_version, scan_time_utc, data_mode,
               universe_size, btc_change_24h, btc_regime,
               health_status, config_hash, git_sha
               , clock_skew_seconds
        FROM scans
        ORDER BY scan_time_utc DESC
        LIMIT 1
        """
    ).fetchone()

    if latest is None:
        Path(REPORT_FILE).write_text(
            "# Binance Avci 2\n\nHenuz tarama yok.\n",
            encoding="utf-8",
        )
        return

    version = latest["config_version"]

    scan_summary = conn.execute(
        """
        SELECT COUNT(*) AS scans,
               MIN(scan_time_utc) AS first_scan,
               MAX(scan_time_utc) AS last_scan
        FROM scans
        WHERE config_version = ?
        """,
        (version,),
    ).fetchone()

    health_rows = conn.execute(
        """
        SELECT health_status, btc_regime, COUNT(*) AS scans
        FROM scans
        WHERE config_version = ?
        GROUP BY health_status, btc_regime
        ORDER BY health_status, btc_regime
        """,
        (version,),
    ).fetchall()

    class_rows = conn.execute(
        """
        SELECT event_class, e.validation_tier, e.data_mode, e.btc_regime,
               COUNT(*) AS events,
               SUM(CASE WHEN o.label_status = 'CLOSED'
                        THEN 1 ELSE 0 END) AS closed,
               AVG(o.net_return_pct) AS avg_net,
               AVG(o.mfe_pct) AS avg_mfe,
               AVG(o.mae_pct) AS avg_mae,
               SUM(CASE WHEN o.mfe_pct >= 3
                        THEN 1 ELSE 0 END) AS reach_3,
               SUM(CASE WHEN o.mfe_pct >= 5
                        THEN 1 ELSE 0 END) AS reach_5,
               SUM(CASE WHEN json_extract(
                    o.barrier_results_json,
                    '$."72"."10.0".result') = 'TARGET'
                    THEN 1 ELSE 0 END) AS reach_10,
               AVG(o.excess_vs_btc_pct) AS avg_excess_btc,
               AVG(o.excess_vs_universe_pct) AS avg_excess_universe
        FROM signal_events e
        LEFT JOIN outcome_labels o
          ON o.event_id = e.event_id
        WHERE e.config_version = ?
        GROUP BY event_class, e.validation_tier, e.data_mode, e.btc_regime
        ORDER BY e.validation_tier, event_class, e.data_mode, e.btc_regime
        """,
        (version,),
    ).fetchall()

    primary_rows = conn.execute(
        """
        SELECT e.event_class, COUNT(*) AS closed,
               SUM(CASE WHEN json_extract(o.barrier_results_json,
                   '$."72"."10.0".result')='TARGET' THEN 1 ELSE 0 END) AS wins,
               AVG(o.excess_vs_btc_pct) AS excess_btc,
               AVG(o.excess_vs_universe_pct) AS excess_universe
        FROM signal_events e JOIN outcome_labels o ON o.event_id=e.event_id
        WHERE e.config_version=? AND e.validation_tier='PRIMARY'
          AND o.label_status='CLOSED'
          AND e.event_class IN ('CANDIDATE','RANDOM_CONTROL')
        GROUP BY e.event_class
        """, (version,),
    ).fetchall()
    primary = {row["event_class"]: row for row in primary_rows}
    regimes = conn.execute(
        """SELECT COUNT(DISTINCT btc_regime) FROM signal_events e
           JOIN outcome_labels o ON o.event_id=e.event_id
           WHERE e.config_version=? AND e.validation_tier='PRIMARY'
             AND e.event_class='CANDIDATE' AND o.label_status='CLOSED'""",
        (version,),
    ).fetchone()[0]

    calibration = conn.execute(
        """
        SELECT e.score, COUNT(*) AS closed,
               AVG(o.net_return_pct) AS avg_net,
               AVG(o.excess_vs_btc_pct) AS avg_excess_btc
        FROM signal_events e JOIN outcome_labels o ON o.event_id=e.event_id
        WHERE e.config_version=? AND e.event_class='CANDIDATE'
          AND o.label_status='CLOSED'
        GROUP BY e.score ORDER BY e.score DESC
        """, (version,),
    ).fetchall()

    journal_rows = conn.execute(
        """SELECT m.*, e.signal_time_utc, e.symbol, o.net_return_pct AS theoretical_net
           FROM manual_trades m LEFT JOIN signal_events e ON e.event_id=m.event_id
           LEFT JOIN outcome_labels o ON o.event_id=m.event_id
           ORDER BY m.execution_time_utc"""
    ).fetchall()

    research_rows = conn.execute(
        """SELECT e.signal_time_utc, e.event_class, e.raw_json,
                  o.net_return_pct, o.excess_vs_btc_pct
           FROM signal_events e JOIN outcome_labels o ON o.event_id=e.event_id
           WHERE e.config_version=? AND e.validation_tier='PRIMARY'
             AND o.label_status='CLOSED'
             AND e.event_class IN ('CANDIDATE','RANDOM_CONTROL')
           ORDER BY e.signal_time_utc""", (version,),
    ).fetchall()

    candidates = conn.execute(
        """
        SELECT e.signal_time_utc, e.symbol, e.stage,
               e.engine, e.score, e.data_mode, e.validation_tier,
               e.spread_bps, e.buy_impact_1k_bps,
               e.gain_before_signal_pct,
               e.minutes_from_first_anomaly,
               o.label_status, o.net_return_pct,
               o.mfe_pct, o.mae_pct,
               o.excess_vs_btc_pct, o.excess_vs_universe_pct,
               json_extract(o.horizon_metrics_json,
                    '$."4".close_return_pct') AS return_4h,
               json_extract(o.horizon_metrics_json,
                    '$."24".close_return_pct') AS return_24h,
               json_extract(o.horizon_metrics_json,
                    '$."72".close_return_pct') AS return_72h
        FROM signal_events e
        LEFT JOIN outcome_labels o
          ON o.event_id = e.event_id
        WHERE e.config_version = ?
          AND e.event_class = 'CANDIDATE'
        ORDER BY e.signal_time_utc DESC
        LIMIT 25
        """,
        (version,),
    ).fetchall()

    issues = conn.execute(
        """
        SELECT issue_type, COUNT(*) AS count_value,
               MAX(issue_time_utc) AS last_time
        FROM data_issues
        WHERE julianday(issue_time_utc)
              >= julianday('now', '-1 day')
        GROUP BY issue_type
        ORDER BY count_value DESC
        """
    ).fetchall()

    lines = [
        "# Binance Avci 2 - Son Rapor",
        "",
        f"- Surum: `{version}`",
        f"- Son tarama (UTC): `{latest['scan_time_utc']}`",
        f"- Veri modu: `{latest['data_mode']}`",
        f"- Evren: `{latest['universe_size']}`",
        f"- BTC 24s: `{latest['btc_change_24h']:+.2f}%`",
        f"- BTC rejimi: `{latest['btc_regime'] or '-'}`",
        f"- Veri sagligi: `{latest['health_status'] or '-'}`",
        f"- Ayar kimligi: `{(latest['config_hash'] or '-')[:12]}`",
        f"- Kod kimligi: `{(latest['git_sha'] or '-')[:12]}`",
        f"- Saat farki: `{fmt(latest['clock_skew_seconds'])} sn`",
        f"- Bu surumde tarama: `{scan_summary['scans']}`",
        f"- Aralik: `{scan_summary['first_scan']}` - "
        f"`{scan_summary['last_scan']}`",
        "",
        "> OPEN sonuclar gecicidir; sonuc 72 saat sonunda kapanir. "
        "Yalnizca PRIMARY satirlari resmi dogrulama sayilir.",
        "",
        "## Veri Sagligi",
        "",
        "| Saglik | BTC rejimi | Tarama |",
        "|---|---|---:|",
    ]

    for row in health_rows:
        lines.append(
            f"| {row['health_status'] or '-'} | {row['btc_regime'] or '-'} | "
            f"{row['scans']} |"
        )

    lines.extend([
        "",
        "## Sinif Ozeti",
        "",
        "| Sinif | Dogrulama | Mod | Rejim | Event | Kapali | Ort. net % | "
        "Ort. MFE % | Ort. MAE % | +3 | +5 | +10 bariyer | "
        "BTC ustu % | Evren ustu % |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])

    for row in class_rows:
        lines.append(
            f"| {row['event_class']} | {row['validation_tier']} | "
            f"{row['data_mode']} | {row['btc_regime'] or '-'} | "
            f"{row['events']} | "
            f"{row['closed']} | {fmt(row['avg_net'])} | "
            f"{fmt(row['avg_mfe'])} | {fmt(row['avg_mae'])} | "
            f"{row['reach_3'] or 0} | {row['reach_5'] or 0} | "
            f"{row['reach_10'] or 0} | "
            f"{fmt(row['avg_excess_btc'])} | "
            f"{fmt(row['avg_excess_universe'])} |"
        )

    lines.extend([
        "",
        "## Son Adaylar",
        "",
        "| UTC | Coin | Asama | Skor | Dogrulama | Spread bps | "
        "$1k etki bps | Durum | 4s % | 24s % | 72s % | Bariyer net % | "
        "MFE % | MAE % | BTC ustu % | Evren ustu % |",
        "|---|---|---|---:|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])

    for row in candidates:
        lines.append(
            f"| {row['signal_time_utc']} | {row['symbol']} | "
            f"{row['stage']} | {row['score']} | {row['validation_tier']} | "
            f"{fmt(row['spread_bps'])} | {fmt(row['buy_impact_1k_bps'])} | "
            f"{row['label_status'] or 'PENDING'} | "
            f"{fmt(row['return_4h'])} | {fmt(row['return_24h'])} | "
            f"{fmt(row['return_72h'])} | {fmt(row['net_return_pct'])} | "
            f"{fmt(row['mfe_pct'])} | {fmt(row['mae_pct'])} | "
            f"{fmt(row['excess_vs_btc_pct'])} | "
            f"{fmt(row['excess_vs_universe_pct'])} |"
        )

    lines.extend([
        "",
        "## Basari Kriteri",
        "",
        "Resmi karar icin en az 100 kapali PRIMARY aday, 100 kapali PRIMARY "
        "random kontrol ve en az 2 BTC rejimi gerekir. Adaylarin +10 bariyer "
        "basari orani kontrol grubundan yuksek, BTC ve evren ustu ortalama "
        "getirileri pozitif olmalidir.",
        "",
    ])

    candidate_primary = primary.get("CANDIDATE")
    control_primary = primary.get("RANDOM_CONTROL")
    ready = bool(candidate_primary and control_primary
                 and candidate_primary["closed"] >= 100
                 and control_primary["closed"] >= 100 and regimes >= 2)
    if not ready:
        lines.append(
            f"- Karar: `VERI_YETERSIZ` (aday={candidate_primary['closed'] if candidate_primary else 0}, "
            f"kontrol={control_primary['closed'] if control_primary else 0}, rejim={regimes})"
        )
    else:
        candidate_rate = 100 * candidate_primary["wins"] / candidate_primary["closed"]
        control_rate = 100 * control_primary["wins"] / control_primary["closed"]
        passed = (candidate_rate > control_rate
                  and (candidate_primary["excess_btc"] or 0) > 0
                  and (candidate_primary["excess_universe"] or 0) > 0)
        lines.append(f"- Karar: `{'GECTI' if passed else 'GECMEDI'}`")

    lines.extend(["", "## %95 Guven Araliklari", "",
                  "| Grup | Kapali | +10 | Oran % | %95 alt | %95 ust |",
                  "|---|---:|---:|---:|---:|---:|"])
    for name in ("CANDIDATE", "RANDOM_CONTROL"):
        row = primary.get(name)
        if not row:
            continue
        low, high = wilson_interval(row["wins"] or 0, row["closed"] or 0)
        rate = 100 * (row["wins"] or 0) / row["closed"] if row["closed"] else 0
        lines.append(f"| {name} | {row['closed']} | {row['wins'] or 0} | "
                     f"{rate:.2f} | {fmt(low)} | {fmt(high)} |")

    lines.extend(["", "## Skor Kalibrasyonu", "",
                  "| Skor | Kapali | Ort. net % | BTC ustu % |",
                  "|---:|---:|---:|---:|"])
    if calibration:
        for row in calibration:
            lines.append(f"| {row['score']} | {row['closed']} | {fmt(row['avg_net'])} | "
                         f"{fmt(row['avg_excess_btc'])} |")
    else:
        lines.append("| - | 0 | - | - |")

    trades = {}
    for row in journal_rows:
        trades.setdefault(row["event_id"], []).append(row)
    lines.extend(["", "## Manuel Trade Journal", "",
                  "| Event | Coin | Tepki dk | Gercek net % | Teorik net % | Fark % |",
                  "|---|---|---:|---:|---:|---:|"])
    journal_count = 0
    for event_id, records in trades.items():
        entry = next((r for r in records if r["action"] == "ENTRY"), None)
        exit_row = next((r for r in reversed(records) if r["action"] == "EXIT"), None)
        if not entry:
            continue
        delay = None
        if entry["signal_time_utc"]:
            from datetime import datetime
            delay = (datetime.fromisoformat(entry["execution_time_utc"])
                     - datetime.fromisoformat(entry["signal_time_utc"])).total_seconds() / 60
        actual = None
        if exit_row and entry["price"]:
            actual = (exit_row["price"] / entry["price"] - 1) * 100
        theoretical = entry["theoretical_net"]
        difference = actual - theoretical if actual is not None and theoretical is not None else None
        lines.append(f"| {event_id} | {entry['symbol'] or '-'} | {fmt(delay, 1)} | "
                     f"{fmt(actual)} | {fmt(theoretical)} | {fmt(difference)} |")
        journal_count += 1
    if journal_count == 0:
        lines.append("| Kayit yok | - | - | - | - | - |")

    grouped_returns = {"CANDIDATE": [], "RANDOM_CONTROL": []}
    for row in research_rows:
        grouped_returns[row["event_class"]].append(float(row["net_return_pct"]))
    def mean(values):
        return sum(values) / len(values) if values else None
    candidate_values = grouped_returns["CANDIDATE"]
    control_values = grouped_returns["RANDOM_CONTROL"]
    cand_recent, cand_prior = candidate_values[-30:], candidate_values[-60:-30]
    ctrl_recent, ctrl_prior = control_values[-30:], control_values[-60:-30]
    recent_alpha = (
        mean(cand_recent) - mean(ctrl_recent)
        if cand_recent and ctrl_recent else None
    )
    prior_alpha = (
        mean(cand_prior) - mean(ctrl_prior)
        if cand_prior and ctrl_prior else None
    )
    lines.extend(["", "## Alpha Decay", "",
                  f"- Son 30 aday-kontrol net farki: `{fmt(recent_alpha)}%`",
                  f"- Onceki 30 aday-kontrol net farki: `{fmt(prior_alpha)}%`",
                  f"- Degisim: `{fmt(recent_alpha-prior_alpha) if recent_alpha is not None and prior_alpha is not None else '-'}%`"])

    lines.extend(["", "## Walk-forward", ""])
    if len(candidate_values) < 100:
        lines.append(f"- `VERI_YETERSIZ`: {len(candidate_values)}/100 kapali PRIMARY aday.")
    else:
        split = int(len(candidate_values) * .70)
        lines.append(f"- Ilk %70 ortalama net: `{fmt(mean(candidate_values[:split]))}%`")
        lines.append(f"- Son %30 out-of-sample ortalama net: `{fmt(mean(candidate_values[split:]))}%`")

    ablation_features = ("wakeup", "persistence", "retention", "reignition", "trigger")
    lines.extend(["", "## Ablation / Ozellik Katkisi", "",
                  "| Ozellik | Acik adet | Acik net % | Kapali adet | Kapali net % | Fark % |",
                  "|---|---:|---:|---:|---:|---:|"])
    candidate_research = [row for row in research_rows if row["event_class"] == "CANDIDATE"]
    for feature_name in ablation_features:
        enabled, disabled = [], []
        for row in candidate_research:
            try:
                payload = json.loads(row["raw_json"] or "{}")
            except json.JSONDecodeError:
                payload = {}
            (enabled if payload.get(feature_name) else disabled).append(float(row["net_return_pct"]))
        difference = mean(enabled) - mean(disabled) if enabled and disabled else None
        lines.append(f"| {feature_name} | {len(enabled)} | {fmt(mean(enabled))} | "
                     f"{len(disabled)} | {fmt(mean(disabled))} | {fmt(difference)} |")

    lines.extend(["", "## Dis Veri Durumu", "",
                  "- Haber/katalizor: `UNAVAILABLE_NO_NEWS_FEED`",
                  "- Sektor ve piyasa degeri: `UNAVAILABLE_NO_MARKET_METADATA_FEED`",
                  "- Tek cuzdan hakimiyeti: `UNAVAILABLE_FOR_CEX_WITHOUT_ONCHAIN_PROVIDER`",
                  "- Para yatirma/cekme durumu: `UNAVAILABLE_WITHOUT_AUTHENTICATED_EXCHANGE_API`",
                  "- Futures/OI/funding: GitHub aginda engellenirse `OBSERVATIONAL`; VPS gerekir."])

    lines.extend([
        "",
        "## Son 24 Saat Veri Sorunlari",
        "",
    ])

    if issues:
        for row in issues:
            lines.append(
                f"- `{row['issue_type']}`: {row['count_value']} "
                f"(son: {row['last_time']})"
            )
    else:
        lines.append("- Kayitli sorun yok.")

    Path(REPORT_FILE).write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    print(f"Rapor yazildi: {REPORT_FILE}")


if __name__ == "__main__":
    main()
