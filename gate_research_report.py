"""Descriptive Gate forward-test report; frozen V5 rules are never tuned here."""

import math
import os
import sqlite3
from datetime import datetime, timezone


RULES = ("R1_WAKEUP_STRICT", "R2_REIGNITION_TRIGGER",
         "R3_PERSISTENCE_STRUCTURE")
GROUPS = ("CANDIDATE", "NEAR_MISS", "RANDOM_CONTROL")


def wilson(successes, total):
    if total <= 0:
        return None
    z = 1.96
    p = successes / total
    denominator = 1 + z*z/total
    center = (p + z*z/(2*total)) / denominator
    width = z * math.sqrt(p*(1-p)/total + z*z/(4*total*total)) / denominator
    return 100 * (center - width), 100 * (center + width)


def summary(rows):
    # Unresolved/data failures are explicitly excluded from the main rate.
    known = [row for row in rows if row["status"] == "CLOSED_72H"
             and row["result_10"] in ("TARGET_FIRST", "STOP_FIRST", "TIMEOUT")]
    hits = sum(row["result_10"] == "TARGET_FIRST" for row in known)
    net = [row["net_final_pct"] for row in known
           if row["cost_status"] == "QUOTE_PLUS_ASSUMPTION"
           and row["net_final_pct"] is not None]
    return {"all": len(rows), "resolved": len(known), "unresolved": len(rows)-len(known),
            "hits": hits, "rate": 100*hits/len(known) if known else None,
            "ci": wilson(hits, len(known)), "net_n": len(net),
            "net_mean": sum(net)/len(net) if net else None}


def build_report(validation_db="avci_validation_v5.db", obs_db="avci2.db",
                 now_ts=None):
    now_ts = now_ts or int(datetime.now(timezone.utc).timestamp())
    if not os.path.exists(validation_db):
        return "# Gate Avcı 2 araştırma raporu\n\nDoğrulama verisi henüz yok.\n"
    with sqlite3.connect(f"file:{validation_db}?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        try:
            rows = [dict(x) for x in con.execute("""SELECT batch_id, network_id,
                token_contract, group_type, rulesets, result_10, status,
                net_final_pct, cost_status, signal_ts FROM validation_events
                WHERE signal_ts >= ? ORDER BY signal_ts""", (now_ts-30*86400,))]
        except sqlite3.OperationalError:
            rows = []
    lines = ["# Gate Avcı 2 | son 30 gün", "",
             "Sadece sinyalden sonraki kapanmış olaylar ölçülür. V5 kuralları sabittir.",
             "Bilinmeyen/veri hatalı sonuçlar başarı veya normal kayıp sayılmaz.", ""]
    overall = {group: summary([r for r in rows if r["group_type"] == group])
               for group in GROUPS}
    lines += ["| Grup | Toplam | Sonucu bilinen | Bilinmeyen | +%10 önce ulaştı | %95 aralık | Net sonuç ölçülebilen |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    for group, s in overall.items():
        rate = f"%{s['rate']:.1f}" if s["rate"] is not None else "—"
        ci = f"%{s['ci'][0]:.1f}–%{s['ci'][1]:.1f}" if s["ci"] else "—"
        lines.append(f"| {group} | {s['all']} | {s['resolved']} | "
                     f"{s['unresolved']} | {rate} | {ci} | {s['net_n']} |")
    lines += ["", "## Dondurulmuş kurallar (örtüşebilir)", ""]
    for rule in RULES:
        subset = [r for r in rows if r["group_type"] == "CANDIDATE"
                  and rule in (r["rulesets"] or "").split(",")]
        s = summary(subset)
        rate = f"%{s['rate']:.1f}" if s["rate"] is not None else "veri yok"
        lines.append(f"- {rule}: {s['resolved']} kapanmış, +%10 önce: {rate}; "
                     f"maliyet sonrası ölçülebilen: {s['net_n']}")
    lines += ["", "## Veri sağlığı ve erken gözlem", ""]
    risk_map = {}
    social_map = {}
    if os.path.exists(obs_db):
        with sqlite3.connect(f"file:{obs_db}?mode=ro", uri=True) as con:
            con.row_factory = sqlite3.Row
            try:
                recent = con.execute("""SELECT COUNT(*),
                    SUM(CASE WHEN status='INVALID' THEN 1 ELSE 0 END),
                    AVG(observed_tokens) FROM gate_scan_health
                    WHERE scan_ts>=?""", (now_ts-86400,)).fetchone()
                lines.append(f"Son 24 saat: {recent[0]} tarama, {recent[1] or 0} "
                             f"geçersiz, ortalama {recent[2] or 0:.1f} gözlenen coin.")
                observed = con.execute("""SELECT COUNT(*),
                    SUM(CASE WHEN observed_anomaly=1 THEN 1 ELSE 0 END)
                    FROM gate_early_observations WHERE scan_ts>=?""",
                    (now_ts-86400,)).fetchone()
                lines.append(f"Erken gözlem: {observed[0]} coin ölçümü, "
                             f"{observed[1] or 0} hacim/işlem akışı anomali kaydı.")
                baseline = con.execute("""SELECT COUNT(*), AVG(observed_tokens)
                    FROM gate_scan_health WHERE scan_ts>=? AND scan_ts<?""",
                    (now_ts-7*86400, now_ts-86400)).fetchone()
                if recent[0] >= 12 and baseline[0] >= 48 and baseline[1]:
                    ratio = (recent[2] or 0)/baseline[1]
                    lines.append(f"Gözlenen coin sayısı / önceki 6 gün: "
                                 f"{ratio:.2f} kat" +
                                 (" — veri kapsamı kayması incelenmeli."
                                  if ratio < .5 or ratio > 2 else "."))
            except sqlite3.OperationalError:
                lines.append("Gözlem geçmişi henüz yok.")
            try:
                risk_map = {(r["batch_id"], r["network_id"], r["token_contract"]):
                            dict(r) for r in con.execute("""SELECT batch_id,
                    network_id, token_contract, lp_protected_pct,
                    top10_adjusted_pct, unique_buyers_sample
                    FROM gate_candidate_risk_history WHERE scan_ts>=?""",
                    (now_ts-30*86400,))}
            except sqlite3.OperationalError:
                pass
            try:
                social_map = {(r["batch_id"], r["network_id"],
                               r["token_contract"]): dict(r)
                              for r in con.execute("""SELECT batch_id,
                    network_id, token_contract, social_status, x_mentions_15m,
                    x_mentions_prev_45m FROM gate_optional_context""")}
            except sqlite3.OperationalError:
                pass
    else:
        lines.append("Gözlem geçmişi henüz yok.")
    lines += ["", "## Önceden seçilmiş güvenlik gözlemleri (yalnızca aday)",
             "", "Bu alt gruplar kontrolle eşleştirilmiş nedensel karşılaştırma "
             "değildir; küçük örneklemle en iyi kombinasyon seçilmez.", ""]
    buckets = (
        ("LP ≥%80 ve ilk 10 holder <%50", lambda x: x["lp_protected_pct"] is not None
         and x["top10_adjusted_pct"] is not None
         and x["lp_protected_pct"] >= 80 and x["top10_adjusted_pct"] < 50),
        ("Örneklenen farklı alıcı ≥10 ve ilk 10 holder <%70",
         lambda x: x["unique_buyers_sample"] is not None
         and x["top10_adjusted_pct"] is not None
         and x["unique_buyers_sample"] >= 10 and x["top10_adjusted_pct"] < 70),
    )
    for label, rule in buckets:
        subset = [row for row in rows if row["group_type"] == "CANDIDATE"
                  and (risk := risk_map.get((row["batch_id"], row["network_id"],
                                             row["token_contract"].lower())))
                  and rule(risk)]
        s = summary(subset)
        rate = f"%{s['rate']:.1f}" if s["rate"] is not None else "veri yok"
        lines.append(f"- {label}: {s['resolved']} kapanmış, +%10 önce: {rate}, "
                     f"maliyet sonrası ölçülebilen: {s['net_n']}")
    social_subset = [row for row in rows if row["group_type"] == "CANDIDATE"
                     and (social := social_map.get((row["batch_id"],
                         row["network_id"], row["token_contract"].lower())))
                     and social["social_status"] == "OBSERVED"
                     and (social["x_mentions_15m"] or 0) >= 3
                     and (social["x_mentions_prev_45m"] or 0) <=
                         social["x_mentions_15m"]]
    s = summary(social_subset)
    rate = f"%{s['rate']:.1f}" if s["rate"] is not None else "veri yok"
    lines.append(f"- X tam kontrat ≥3/15dk ve önceki 45dk'dan fazla: "
                 f"{s['resolved']} kapanmış, +%10 önce: {rate}. "
                 "Sosyal veri opsiyoneldir; ilgiyi organik saymayın.")
    if min(overall["CANDIDATE"]["resolved"],
           overall["RANDOM_CONTROL"]["resolved"]) < 100:
        lines += ["", "**Karar:** En az 100 kapanmış aday ve kontrol olmadan "
                  "filtre kombinasyonu başarılı ilan edilmez; eşikler değiştirilmez."]
    else:
        a, b = overall["CANDIDATE"], overall["RANDOM_CONTROL"]
        lines += ["", f"Aday ile rastgele kontrol farkı: "
                  f"{a['rate']-b['rate']:+.1f} yüzde puan. "
                  "Bu fark tek başına nedensellik veya gelecek kâr garantisi değildir."]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    result = build_report()
    print(result)
    output = os.getenv("GITHUB_STEP_SUMMARY")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(result)
