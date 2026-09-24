#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Readable Telegram research report for History Miner."""
import os, sqlite3, statistics
import requests
from binance_notify import resolve_chat_id, send_telegram

DB=os.getenv("HISTORY_DB","history_miner.db")
ACTIVATION_FEATURES=[
 ("ret_1d","son 1 günlük getiri"),
 ("ret_3d","son 3 günlük getiri"),
 ("ret_7d","son 7 günlük getiri"),
 ("vol_ratio_1_30","son gün hacmi / 30g normal hacim"),
 ("vol_ratio_3_30","son 3g hacmi / 30g normal hacim"),
 ("range_ratio_1_30","son gün hareket genişliği / 30g normali"),
 ("close_location_1d","kapanışın gün içi gücü"),
 ("dist_low_7d","7 günlük dipten uzaklaşma"),
 ("dist_high_30d","30 günlük tepeye uzaklık"),
 ("green_ratio_3d","son 3 gün yeşil oranı"),
]

FEATURES=[
 ("ret_7d","7 günlük getiri"),
 ("ret_30d","30 günlük getiri"),
 ("ret_90d","90 günlük getiri"),
 ("drawdown_30d","30 günlük geri çekilme"),
 ("vol_ratio_7_30","7g/30g hacim oranı"),
 ("vol_ratio_30_90","30g/90g hacim oranı"),
 ("realized_vol_30d","30 günlük oynaklık"),
 ("range_compression_7_30","7g/30g sıkışma oranı"),
 ("green_ratio_14d","14 günlük yeşil gün oranı"),
 ("dist_high_90d","90 günlük tepeye uzaklık"),
]


PLAIN_EXPLAIN={
 "14 günlük yeşil gün oranı":"→ Patlayan coinler öncesinde sürekli yükselen coinler değil; daha zayıf/kararsız bir dönem geçiriyor.",
 "90 günlük tepeye uzaklık":"→ Patlayan coinler genelde eski zirvesinden daha uzakta; yani önceden daha fazla ezilmiş oluyor.",
 "7 günlük getiri":"→ Patlamadan önceki son hafta genellikle daha zayıf geçiyor.",
 "30 günlük geri çekilme":"→ Patlayan coinler son 30 günde daha sert geri çekilmiş oluyor.",
 "30 günlük getiri":"→ Son bir aylık geçmiş performansın patlamadan önce nasıl davrandığını gösterir.",
 "90 günlük getiri":"→ Uzun süredir zayıf kalan coinlerde patlama öncesi ortak yapı olup olmadığını gösterir.",
 "7g/30g hacim oranı":"→ Son haftadaki hacmin son 30 güne göre hızlanıp hızlanmadığını gösterir.",
 "30g/90g hacim oranı":"→ Son ay hacminin daha eski normale göre değişip değişmediğini gösterir.",
 "30 günlük oynaklık":"→ Coinin patlama öncesi ne kadar dalgalı olduğunu gösterir.",
 "7g/30g sıkışma oranı":"→ Son hafta hareket alanının daralıp daralmadığını; yani sıkışma olup olmadığını gösterir.",
}

PATH_EXPLAIN={
 "24s dipten uzaklaşma":"→ Coin dipten yavaş yavaş ayrılmaya başlıyor.",
 "24 saatlik fiyat":"→ Fiyat hareketi artık görünür hale geliyor.",
 "6 saatlik fiyat":"→ Son saatlerde momentum belirginleşiyor; bu erken sinyalden çok hareketin başlangıcı olabilir.",
 "3s hacim / önceki 24s normali":"→ İlk erken işaret hacimde başlıyor; fiyat patlamadan önce aktivite artıyor olabilir.",
 "1s hacim / önceki 24s normali":"→ Kısa vadeli hacim normalin üzerine çıkıyor.",
}

def human_feature(feature):
    mapping={
      "dist_high_90d":"90 günlük zirveye uzaklık",
      "drawdown_30d":"30 günlük geri çekilme",
      "ret_90d":"90 günlük getiri",
      "ret_30d":"30 günlük getiri",
      "ret_7d":"7 günlük getiri",
      "green_ratio_14d":"14 günlük yeşil gün oranı",
      "realized_vol_30d":"30 günlük oynaklık",
      "range_compression_7_30":"7g/30g sıkışma oranı",
      "vol_ratio_7_30":"7g/30g hacim oranı",
      "vol_ratio_30_90":"30g/90g hacim oranı",
      "vol_ratio_3_24":"3s hacim / önceki 24s normali",
      "vol_ratio_1_24":"1s hacim / önceki 24s normali",
      "dist_low_24h":"24s dipten uzaklaşma",
      "ret_24h":"24 saatlik fiyat",
      "ret_6h":"6 saatlik fiyat",
      "ret_3h":"3 saatlik fiyat",
      "ret_1h":"1 saatlik fiyat",
    }
    return mapping.get(feature,feature)

def med(vals):
    vals=[float(x) for x in vals if x is not None]
    return statistics.median(vals) if vals else None

def latest_run(c):
    row=c.execute("""SELECT pairs_attempted,pairs_ok,bars,events,controls
        FROM miner_stats ORDER BY started_utc DESC LIMIT 1""").fetchone()
    return row or (0,0,0,0,0)

def totals(c):
    return (
      c.execute("SELECT COUNT(*) FROM cex_pairs").fetchone()[0],
      c.execute("SELECT COUNT(*) FROM cex_pairs WHERE status='DONE'").fetchone()[0],
      c.execute("SELECT COUNT(*) FROM cex_pairs WHERE status='SKIP_SHORT'").fetchone()[0],
      c.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0],
      c.execute("SELECT COUNT(*) FROM rise_events").fetchone()[0],
      c.execute("SELECT COUNT(*) FROM event_features WHERE label='CONTROL'").fetchone()[0],
    )

def strongest_differences(c, limit=4):
    out=[]
    for col,label in FEATURES:
        rise=[r[0] for r in c.execute(f"SELECT {col} FROM event_features WHERE label='RISE' AND {col} IS NOT NULL")]
        ctl=[r[0] for r in c.execute(f"SELECT {col} FROM event_features WHERE label='CONTROL' AND {col} IS NOT NULL")]
        if len(rise)<20 or len(ctl)<20:
            continue
        rm,cm=med(rise),med(ctl)
        if rm is None or cm is None:
            continue
        scale=statistics.median([abs(float(x)-cm) for x in ctl if x is not None]) if ctl else 0
        if not scale or scale<1e-9:
            scale=max(abs(cm),1.0)
        score=abs(rm-cm)/scale
        direction="daha yüksek" if rm>cm else "daha düşük"
        out.append((score,label,direction,rm,cm,len(rise),len(ctl)))
    out.sort(reverse=True,key=lambda x:x[0])
    return out[:limit]

def strongest_activation_differences(c, limit=5):
    exists=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='activation_features'").fetchone()
    if not exists: return []
    out=[]
    for col,label in ACTIVATION_FEATURES:
        rise=[r[0] for r in c.execute(f"SELECT {col} FROM activation_features WHERE label='RISE' AND {col} IS NOT NULL")]
        ctl=[r[0] for r in c.execute(f"SELECT {col} FROM activation_features WHERE label='CONTROL' AND {col} IS NOT NULL")]
        if len(rise)<20 or len(ctl)<20: continue
        rm,cm=med(rise),med(ctl)
        if rm is None or cm is None: continue
        scale=statistics.median([abs(float(x)-cm) for x in ctl if x is not None]) if ctl else 0
        if not scale or scale<1e-9: scale=max(abs(cm),1.0)
        score=abs(rm-cm)/scale
        direction="daha yüksek" if rm>cm else "daha düşük"
        out.append((score,label,direction,rm,cm,len(rise),len(ctl)))
    out.sort(reverse=True,key=lambda x:x[0])
    return out[:limit]

PATH_FEATURES=[
 ("ret_1h","1 saatlik fiyat"),
 ("ret_3h","3 saatlik fiyat"),
 ("ret_6h","6 saatlik fiyat"),
 ("ret_12h","12 saatlik fiyat"),
 ("ret_24h","24 saatlik fiyat"),
 ("vol_ratio_1_24","1s hacim / önceki 24s normali"),
 ("vol_ratio_3_24","3s hacim / önceki 24s normali"),
 ("range_ratio_1_24","saatlik hareket genişliği"),
 ("close_location","saatlik kapanış gücü"),
 ("dist_low_24h","24s dipten uzaklaşma"),
 ("dist_high_24h","24s tepeye uzaklık"),
]

def hourly_path_summary(c, min_n=8):
    exists=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='hourly_path_snapshots'").fetchone()
    if not exists: return [],None,(0,0)
    counts=c.execute("""SELECT label,COUNT(DISTINCT pair||':'||anchor_ts)
      FROM hourly_path_snapshots GROUP BY label""").fetchall()
    cm={r[0]:r[1] for r in counts}
    out=[]
    earliest=None
    for off in (-72,-48,-24,-12,-6,-3,-1):
        best=None
        for col,label in PATH_FEATURES:
            rise=[r[0] for r in c.execute(f"SELECT {col} FROM hourly_path_snapshots WHERE label='RISE' AND offset_h=? AND {col} IS NOT NULL",(off,))]
            ctl=[r[0] for r in c.execute(f"SELECT {col} FROM hourly_path_snapshots WHERE label='CONTROL' AND offset_h=? AND {col} IS NOT NULL",(off,))]
            if len(rise)<min_n or len(ctl)<min_n: continue
            rm,ctm=med(rise),med(ctl)
            if rm is None or ctm is None: continue
            scale=statistics.median([abs(float(x)-ctm) for x in ctl if x is not None]) if ctl else 0
            if not scale or scale<1e-9: scale=max(abs(ctm),1.0)
            score=abs(rm-ctm)/scale
            direction="daha yüksek" if rm>ctm else "daha düşük"
            cand=(score,off,label,direction,rm,ctm,len(rise),len(ctl))
            if best is None or cand[0]>best[0]: best=cand
        if best:
            out.append(best)
            if earliest is None and best[0]>=0.5: earliest=best
    return out,earliest,(cm.get("RISE",0),cm.get("CONTROL",0))

def validation_summary(c):
    exists=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='validation_runs'").fetchone()
    if not exists:
        return None,[],[],[],[]
    run=c.execute("""SELECT cutoff_utc,cases,matches,replicated,tested,notes,version
        FROM validation_runs ORDER BY started_utc DESC LIMIT 1""").fetchone()
    if not run:
        return None,[],[],[],[]
    rows=c.execute("""SELECT feature_scope,feature,offset_h,regime,
        discovery_effect,validation_effect,direction_replicated,validation_grade,phase,
        discovery_rise_n,discovery_control_n,validation_rise_n,validation_control_n
        FROM validation_results
        WHERE version=? AND regime='ALL' AND discovery_effect IS NOT NULL AND validation_effect IS NOT NULL
        ORDER BY CASE validation_grade WHEN 'STRONG' THEN 0 WHEN 'CONSISTENT' THEN 1
                 WHEN 'DIRECTION_ONLY_WEAK' THEN 2 WHEN 'DIRECTION_ONLY_LOW_N' THEN 3
                 ELSE 4 END, ABS(validation_effect) DESC LIMIT 6""",(run[6],)).fetchall()
    regimes=c.execute("""SELECT split,label,btc_regime,n
        FROM validation_regime_counts
        WHERE version=?
        ORDER BY split,label,btc_regime""",(run[6],)).fetchall()
    specs=c.execute("""SELECT spec_key,spec_value FROM validation_spec
        WHERE version=?
        ORDER BY spec_key""",(run[6],)).fetchall()
    regime_rows=c.execute("""SELECT feature_scope,feature,offset_h,regime,
        validation_grade,discovery_effect,validation_effect,
        validation_rise_n,validation_control_n
        FROM validation_results
        WHERE version=? AND regime IN ('UP','SIDEWAYS','DOWN')
          AND discovery_effect IS NOT NULL AND validation_effect IS NOT NULL""",(run[6],)).fetchall()
    return run,rows,regimes,specs,regime_rows

def diagnostics_summary(c):
    exists=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='validation_diagnostics'").fetchone()
    if not exists:
        return {}
    out={}
    for row in c.execute("""SELECT diag_key,scope,feature,offset_h,value,text_value
        FROM validation_diagnostics
        WHERE version='history-diagnostics-v0.1-20260924'"""):
        out[(row[0],row[1],row[2],int(row[3] or 0))]=(row[4],row[5])
    return out

def v2_summary(c):
    exists=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='v2_base_rates'").fetchone()
    if not exists:
        return [],[]
    rates=c.execute("""SELECT horizon_days,threshold_pct,eligible_n,hit_n,base_rate
      FROM v2_base_rates
      WHERE version='history-v2-outcomes-matched-v0.1-20260924'
      ORDER BY horizon_days,threshold_pct""").fetchall()
    results=c.execute("""SELECT horizon_days,threshold_pct,feature,
      validation_effect,validation_grade,validation_rise_n,validation_control_n
      FROM v2_matched_results
      WHERE version='history-v2-outcomes-matched-v0.1-20260924'
        AND validation_grade IN ('STRONG','CONSISTENT')
      ORDER BY CASE validation_grade WHEN 'STRONG' THEN 0 ELSE 1 END,
               ABS(validation_effect) DESC
      LIMIT 8""").fetchall()
    return rates,results

def v3_summary(c):
    exists=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='v3_summary'").fetchone()
    if not exists:
        return [],None
    rows=c.execute("""SELECT horizon_days,threshold_pct,feature,
      raw_rise_n,nonoverlap_rise_n,unique_coin_n,overlap_reduction_pct,
      folds_tested,same_direction_folds,effect_pass_folds,
      median_fold_effect,overall_effect
      FROM v3_summary
      WHERE version='history-v3-stress-v0.1-20260924'
        AND folds_tested>=3 AND same_direction_folds>=3
      ORDER BY effect_pass_folds DESC, threshold_pct DESC,
               ABS(COALESCE(median_fold_effect,0)) DESC
      LIMIT 8""").fetchall()
    avg=c.execute("""SELECT AVG(overlap_reduction_pct)
      FROM v3_summary WHERE version='history-v3-stress-v0.1-20260924'""").fetchone()
    return rows,(float(avg[0]) if avg and avg[0] is not None else None)

def v4_summary(c):
    exists=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='v4_pattern_results'").fetchone()
    if not exists:
        return [],[]
    version='history-v4-pattern-first-v0.1-20260925'
    counts=c.execute("""SELECT split,pattern_name,raw_matching_days,first_entry_signals,unique_coin_n
      FROM v4_pattern_counts WHERE version=?
      ORDER BY split,pattern_name""",(version,)).fetchall()
    rows=c.execute("""SELECT pattern_name,horizon_days,threshold_pct,signal_n,hit_n,miss_n,
      unique_coin_n,precision,false_positive_rate,recall,raw_base_rate,lift_vs_raw_base
      FROM v4_pattern_results
      WHERE version=? AND split='VALIDATION' AND threshold_pct IN (50,100)
        AND signal_n>=20
      ORDER BY threshold_pct DESC,lift_vs_raw_base DESC,signal_n DESC
      LIMIT 8""",(version,)).fetchall()
    return counts,rows

def v5_summary(c):
    exists=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='v5_runs'").fetchone()
    if not exists:
        return None,[],[]
    version='history-v5-activation-v0.1-20260925'
    run=c.execute("""SELECT discovery_total,discovery_done,validation_total,validation_done,
      result_rows,spec_frozen,notes
      FROM v5_runs WHERE version=? ORDER BY started_utc DESC LIMIT 1""",(version,)).fetchone()
    specs=c.execute("""SELECT feature,operator,threshold,quantile
      FROM v5_activation_spec WHERE version=? ORDER BY feature""",(version,)).fetchall()
    rows=[]
    if run and int(run[5] or 0)==1:
        rows=c.execute("""SELECT pattern_name,activation_name,horizon_days,threshold_pct,
          signal_n,hit_n,unique_coin_n,precision,false_positive_rate,parent_precision,
          lift_vs_parent,raw_base_rate,lift_vs_raw_base,p_value,q_value,fdr_pass
          FROM v5_activation_results
          WHERE version=? AND split='VALIDATION' AND activation_name!='BASE'
            AND signal_n>=20
          ORDER BY fdr_pass DESC, COALESCE(q_value,1), COALESCE(lift_vs_parent,0) DESC
          LIMIT 8""",(version,)).fetchall()
    return run,specs,rows

def cross_venue_summary(c):
    exists=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cross_venue_cases'").fetchone()
    if not exists:
        return None

    version='cross-venue-v0.1-20260924'
    counts={}
    for group,status,n in c.execute("""SELECT venue_group,history_status,COUNT(*)
      FROM cross_venue_cases WHERE version=?
      GROUP BY venue_group,history_status""",(version,)):
        counts[(group,status)]=int(n)

    leads=[float(r[0]) for r in c.execute("""SELECT lead_hours
      FROM cross_venue_cases WHERE version=? AND label='RISE'
        AND lead_hours IS NOT NULL""",(version,))]
    lead_med=statistics.median(leads) if leads else None
    lead_q1=lead_q3=None
    if leads:
        s=sorted(leads)
        qs=statistics.quantiles(s,n=4,method="inclusive") if len(s)>=2 else [s[0],s[0],s[0]]
        lead_q1,lead_q3=qs[0],qs[2]
    lead_bins={
      "binance_6h_early":sum(1 for x in leads if x<=-6),
      "near_1h":sum(1 for x in leads if -1<=x<=1),
      "gate_6h_late":sum(1 for x in leads if x>=6),
      "binance_12h_early":sum(1 for x in leads if x<=-12),
      "gate_12h_late":sum(1 for x in leads if x>=12),
    }

    def earliest(group,venue):
        rows=c.execute("""SELECT feature,offset_h,rise_n,control_n,effect
          FROM cross_venue_summary
          WHERE version=? AND venue_group=? AND venue=?
            AND effect IS NOT NULL
          ORDER BY offset_h ASC""",(version,group,venue)).fetchall()
        candidates=[]
        for feature,off,nr,nc,eff in rows:
            if int(nr)>=20 and int(nc)>=20 and abs(float(eff))>=0.50:
                candidates.append((int(off),feature,float(eff),int(nr),int(nc)))
        if not candidates:
            return None
        # Earliest means farthest before t0: -72 first, then -48, etc.
        return sorted(candidates,key=lambda x:x[0])[0]

    return {
      "counts":counts,
      "lead_median":lead_med,
      "lead_q1":lead_q1,
      "lead_q3":lead_q3,
      "lead_bins":lead_bins,
      "lead_n":len(leads),
      "binance_earliest":earliest("BINANCE_SYMBOL_MATCH","BINANCE"),
      "gate_shared_earliest":earliest("BINANCE_SYMBOL_MATCH","GATE"),
      "gate_only_earliest":earliest("GATE_ONLY","GATE"),
    }

def fmt(x):
    if x is None: return "—"
    ax=abs(x)
    if ax>=1000: return f"{x:,.0f}"
    if ax>=10: return f"{x:.1f}"
    return f"{x:.2f}"

def split_telegram_report(text, limit=3800):
    """Keep logical research sections together whenever possible."""
    lines=text.splitlines()

    # Build logical sections from emoji/title headers. Continuation/explanation
    # lines remain attached to the section they explain.
    sections=[]
    current=[]
    for line in lines:
        is_header=(
            line.startswith("📚 ") or
            line.startswith("🔎 ") or
            line.startswith("⚡ ") or
            line.startswith("🎞 ") or
            line.startswith("🧪 ") or
            line.startswith("🧠 ") or
            line.startswith("🧯 ") or
            line.startswith("🧭 ") or
            line.startswith("🧱 ") or
            line.startswith("🔄 ") or
            line.startswith("🧮 ") or
            line.startswith("🚦 ") or
            line.startswith("📌 ")
        )
        if is_header and current:
            sections.append("\n".join(current).strip())
            current=[line]
        else:
            current.append(line)
    if current:
        sections.append("\n".join(current).strip())

    chunks=[]
    current_chunk=""
    for section in sections:
        candidate=section if not current_chunk else current_chunk+"\n\n"+section
        if current_chunk and len(candidate)>limit:
            chunks.append(current_chunk)
            current_chunk=section
        else:
            current_chunk=candidate
    if current_chunk:
        chunks.append(current_chunk)

    # Last-resort safety for an unusually large single section.
    safe=[]
    for chunk in chunks:
        if len(chunk)<=limit:
            safe.append(chunk)
            continue
        cur=[]
        cur_len=0
        for line in chunk.splitlines():
            extra=len(line)+(1 if cur else 0)
            if cur and cur_len+extra>limit:
                safe.append("\n".join(cur))
                cur=[line]
                cur_len=len(line)
            else:
                cur.append(line)
                cur_len+=extra
        if cur:
            safe.append("\n".join(cur))
    return safe

def main():
    if not os.path.exists(DB):
        print("History report: DB yok"); return
    token=(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    configured=(os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if not token:
        print("History report: Telegram token yok"); return
    with sqlite3.connect(DB,timeout=20) as c:
        run=latest_run(c)
        total=totals(c)
        diffs=strongest_differences(c)
        activation_diffs=strongest_activation_differences(c)
        path_rows,path_earliest,path_counts=hourly_path_summary(c)
        val_run,val_rows,val_regimes,val_specs,val_regime_rows=validation_summary(c)
        diagnostics=diagnostics_summary(c)
        v2_rates,v2_results=v2_summary(c)
        v3_rows,v3_overlap_avg=v3_summary(c)
        v4_counts,v4_rows=v4_summary(c)
        v5_run,v5_specs,v5_rows=v5_summary(c)
        cross_venue=cross_venue_summary(c)
    attempted,ok,bars,events,controls=run
    pairs,done,short_skips,total_bars,total_events,total_controls=total
    lines=[
      "⛏ GEÇMİŞ KAZICI | SON TUR",
      f"• Bu tur taranan parite: {attempted} (başarılı {ok})",
      f"• Bu tur işlenen günlük mum: {bars:,}",
      f"• Bu tur bulunan yükseliş olayı: {events}",
      f"• Bu tur bulunan kontrol dönemi: {controls}",
      "",
      "📚 TOPLAM ARŞİV",
      f"• Geçmişi işlenen parite: {done}/{pairs}",
      f"• Yetersiz geçmiş nedeniyle ayrılan: {short_skips}",
      f"• Günlük mum: {total_bars:,}",
      f"• Yükseliş olayı: {total_events}",
      f"• Kontrol dönemi: {total_controls}",
    ]
    if diffs:
        lines += ["","🔎 ŞİMDİLİK EN BELİRGİN FARKLAR"]
        for _score,label,direction,rm,cm,nr,nc in diffs:
            lines.append(f"• {label}: yükselişlerde {direction} (medyan {fmt(rm)} vs {fmt(cm)})")
            exp=PLAIN_EXPLAIN.get(label)
            if exp: lines.append(f"  {exp}")
        lines.append("")
        lines.append("Not: Bunlar henüz korelasyon. Örneklem büyüdükçe kalıcı mı, tesadüf mü göreceğiz.")
    else:
        lines += ["","🔎 BENZERLİK","• Henüz karşılaştırma için yeterli yükseliş/kontrol örneği yok."]

    if activation_diffs:
        lines += ["","⚡ UYANIŞ İZLERİ | PATLAYAN vs PATLAMAYAN"]
        for _score,label,direction,rm,cm,nr,nc in activation_diffs:
            lines.append(f"• {label}: patlayanlarda {direction} (medyan {fmt(rm)} vs {fmt(cm)})")
        lines += ["","Yorum: Burada aradığımız şey 'düşmüş coin' değil; düşmüşken içeride aktivitesi değişmeye başlayan coin."]

    pr,pc=path_counts
    if pr or pc:
        lines += ["",f"🎞 72 SAATLİK GERİ SARMA | örnek: {pr} yükseliş / {pc} kontrol"]
        if path_rows:
            for score,off,label,direction,rm,cm,nr,nc in path_rows[-4:]:
                lines.append(f"• T{off}s: {label} patlayanlarda {direction} ({fmt(rm)} vs {fmt(cm)})")
                exp=PATH_EXPLAIN.get(label)
                if exp: lines.append(f"  {exp}")
            if path_earliest:
                _score,off,label,direction,rm,cm,nr,nc=path_earliest
                lines.append(f"• İlk belirgin ayrışma adayı: T{off}s — {label}")
                lines.append("  → Yani sistemin şu an gördüğü en erken fark, büyük yükselişten yaklaşık bu kadar önce başlıyor.")
            lines.append("Not: Saatlik film yeterli kontrol örneği biriktikçe güvenilirleşecek.")
        else:
            lines.append("• Saatlik örnekler toplanıyor; karşılaştırma için henüz yeterli iki taraflı veri yok.")
    if val_run:
        cutoff,cases,matches,replicated,tested,notes,val_version=val_run
        lines += ["","🧪 DOĞRULAMA KATMANI",
          f"• Zaman ayrımı: keşif < {cutoff} / doğrulama ≥ {cutoff}",
          f"• Etiketlenen vaka: {cases:,} | eşleşmiş kontrol: {matches:,}",
          f"• Tutarlı/güçlü doğrulama: {replicated}/{tested}",
          "  → Test edilen işaretlerin sadece bu kadarı yeni dönemde de yeterince sağlam kaldı.",
          "• T-72/T-48/T-24/T-12 = erken kanıt; T-6/T-3/T-1 = hareket başlamış olabilir."]
        if val_regimes:
            lines.append("• Rejim dağılımı:")
            for split in ("DISCOVERY","VALIDATION"):
                parts=[]
                for regime in ("UP","SIDEWAYS","DOWN","UNKNOWN"):
                    n=sum(int(r[3]) for r in val_regimes if r[0]==split and r[2]==regime)
                    if n: parts.append(f"{regime}={n}")
                if parts: lines.append(f"  - {split}: " + ", ".join(parts))
        if val_rows:
            lines.append("• Doğrulama durumu:")
            grade_tr={
              "STRONG":"GÜÇLÜ",
              "CONSISTENT":"TUTARLI",
              "DIRECTION_ONLY_WEAK":"AYNI YÖN AMA ZAYIF",
              "DIRECTION_ONLY_LOW_N":"AYNI YÖN AMA ÖRNEK AZ",
              "FAILED_DIRECTION":"YÖN TEKRARLANMADI",
              "INSUFFICIENT":"YETERSİZ"
            }
            for scope,feature,off,regime,de,ve,repl,grade,phase,drn,dcn,vrn,vcn in val_rows[:4]:
                where=f"T{off}s " if scope=="HOURLY" else ""
                regime_parts=[]
                for rr in val_regime_rows:
                    rscope,rfeature,roff,rregime,rgrade,rde,rve,rrn,rcn=rr
                    if rscope==scope and rfeature==feature and int(roff)==int(off):
                        short={
                          "STRONG":"GÜÇLÜ",
                          "CONSISTENT":"TUTARLI",
                          "DIRECTION_ONLY_WEAK":"ZAYIF",
                          "DIRECTION_ONLY_LOW_N":"ÖRNEK AZ",
                          "FAILED_DIRECTION":"YOK",
                          "INSUFFICIENT":"YETERSİZ"
                        }.get(rgrade,rgrade)
                        regime_parts.append(f"{rregime}:{short}")
                best_regime=None
                best_rank=-1
                best_effect=-1.0
                rank_map={"STRONG":4,"CONSISTENT":3,"DIRECTION_ONLY_WEAK":2,"DIRECTION_ONLY_LOW_N":1}
                regime_name={"UP":"BTC YÜKSELİRKEN","SIDEWAYS":"BTC YATAYKEN","DOWN":"BTC DÜŞERKEN"}
                for rr in val_regime_rows:
                    rscope,rfeature,roff,rregime,rgrade,rde,rve,rrn,rcn=rr
                    if rscope==scope and rfeature==feature and int(roff)==int(off):
                        rank=rank_map.get(rgrade,0)
                        eff=abs(float(rve)) if rve is not None else -1.0
                        if rank>best_rank or (rank==best_rank and eff>best_effect):
                            best_rank=rank; best_effect=eff; best_regime=(rregime,rgrade)
                suffix=(" | rejim " + ", ".join(regime_parts)) if regime_parts else ""
                if best_regime and best_rank>0:
                    suffix += f" | EN GÜVENİLİR: {regime_name.get(best_regime[0],best_regime[0])}"
                hname=human_feature(feature)
                lines.append(f"  - {where}{hname}: {grade_tr.get(grade,grade)} | keşif {fmt(de)} → doğrulama {fmt(ve)} | n={vrn}/{vcn}{suffix}")
                if grade=="STRONG":
                    lines.append("    → Eski dönemde de yeni dönemde de net biçimde tekrar etti.")
                elif grade=="CONSISTENT":
                    lines.append("    → İki dönemde de aynı yapı var; güçlü ama kusursuz değil.")
                elif grade=="DIRECTION_ONLY_WEAK":
                    lines.append("    → Aynı yönde bir iz var ama tek başına güvenilecek kadar güçlü değil.")
                elif grade=="DIRECTION_ONLY_LOW_N":
                    lines.append("    → Aynı yönde görünüyor ama karar vermek için örnek sayısı az.")
                elif grade=="FAILED_DIRECTION":
                    lines.append("    → Eski dönemdeki davranış yeni dönemde tekrar etmedi.")
        if path_earliest:
            _score,early_off,early_label,_direction,_rm,_cm,_nr,_nc=path_earliest
            lines += ["","🧠 ŞU ANKİ BASİT HİKÂYE",
              "• Önceden ezilmiş / eski zirvesinden uzak coinler daha sık öne çıkıyor.",
              f"• İlk erken fark yaklaşık T{early_off} civarında {early_label} tarafında görülüyor.",
              "• Sonra coin dipten uzaklaşıyor; T-6/T-3/T-1'e gelince hareket zaten görünürleşiyor.",
              "• Henüz canlı alım kuralı değil; araştırma ve doğrulama devam ediyor."]
        br=diagnostics.get(("hit20_rate","BASE_RATE","",0),(None,None))[0]
        ft=diagnostics.get(("fdr_tested","MULTIPLE_TEST","",0),(None,None))[0]
        fp=diagnostics.get(("fdr_passed","MULTIPLE_TEST","",0),(None,None))[0]
        lines += ["","🧯 SAĞLAMLIK KONTROLLERİ"]
        if br is not None:
            lines.append(f"• +20% / 60 gün taban oranı: %{100*float(br):.1f}")
            if br>=0.50:
                lines.append("  → Bu hedef piyasada çok sık oluyor; tek başına seçici bir 'patlama' etiketi sayılmaz.")
            elif br>=0.25:
                lines.append("  → Bu hedef orta sıklıkta görülüyor; sinyal gücünü yorumlarken taban oranı mutlaka hesaba katılmalı.")
            else:
                lines.append("  → Bu hedef görece seçici; yine de tek başına başarı ölçüsü değil.")
        if ft is not None and fp is not None:
            lines.append(f"• Çoklu test kontrolü (FDR): {int(fp)}/{int(ft)} test ek filtreden geçti.")
            lines.append("  → Çok sayıda özelliği aynı anda denediğimiz için, tesadüfen iyi görünenleri ayıklamak amacıyla ek kontrol uygulanıyor.")
        if v2_rates:
            lines += ["","🧭 V2 | BÜYÜK HAREKET TESTİ"]
            if v2_results:
                lines.append("• Eşleşmiş kontrollerle şimdilik en sağlam V2 sonuçları:")
                for h,t,feature,ve,grade,nr,nc in v2_results[:5]:
                    gt="GÜÇLÜ" if grade=="STRONG" else "TUTARLI"
                    lines.append(f"  - {h}g +%{t} | {human_feature(feature)}: {gt} | etki {fmt(ve)} | n={nr}/{nc}")
                lines.append("  → Bunlar yalnızca eşleşmiş kontrollerle hesaplanan sonuçlar.")
            for h in (7,14,30,60):
                vals={int(r[1]):float(r[4]) for r in v2_rates if int(r[0])==h}
                if vals:
                    parts=[]
                    for t in (20,50,100):
                        if t in vals: parts.append(f"+%{t}: %{100*vals[t]:.1f}")
                    lines.append(f"• {h} gün içinde: " + " | ".join(parts))
            lines.append("  → Amaç: erken yapının sıradan +20 yerine +50/+100 hareketlerde güçlenip güçlenmediğini görmek.")

        if v3_rows:
            lines += ["","🧱 V3 | STRES TESTİ"]
            if v3_overlap_avg is not None:
                lines.append(f"• Aynı coin yakın olaylarını tekilleştirince ortalama olay azalması: %{v3_overlap_avg:.1f}")
            lines.append("• En az 3 ayrı zaman penceresinde aynı yönü koruyan sonuçlar:")
            for h,t,feature,raw_n,ind_n,unique_n,red,folds,same,passed,med_eff,overall_eff in v3_rows[:5]:
                lines.append(
                    f"  - {h}g +%{t} | {human_feature(feature)} | "
                    f"yön {same}/{folds} dönem | güçlü-etki {passed}/{folds} | "
                    f"etki medyan {fmt(med_eff)} | olay {raw_n}→{ind_n} | benzersiz coin {unique_n}"
                )
            lines.append("  → Bu katman V2'yi değiştirmez; aynı bulguları overlap azaltılmış ve 4 zaman penceresinde daha sert sınar.")
            lines.append("  → BTC rejimi 'en güvenilir' etiketleri bağımsız doğrulama gelene kadar yalnızca hipotez kabul edilir.")

        if cross_venue:
            lines += ["","🔄 CROSS-VENUE | GATE vs BINANCE"]
            counts=cross_venue["counts"]
            matched=sum(n for (g,s),n in counts.items() if g=="BINANCE_SYMBOL_MATCH" and s in ("OK","PARTIAL"))
            gate_only=sum(n for (g,s),n in counts.items() if g=="GATE_ONLY")
            nohist=sum(n for (g,s),n in counts.items() if g=="BINANCE_SYMBOL_MATCH" and s=="NO_EVENT_TIME_HISTORY")
            lines.append(f"• Binance saatlik karşılaştırması hazır: {matched} olay | Gate-only: {gate_only} | Binance'de olay zamanı geçmişi yok: {nohist}")
            if cross_venue["lead_median"] is not None:
                lead=float(cross_venue["lead_median"])
                lines.append(f"• +10% geçişi lead/lag medyanı: {lead:+.1f} saat | n={cross_venue['lead_n']} (negatif = Binance daha erken)")
                if cross_venue["lead_q1"] is not None and cross_venue["lead_q3"] is not None:
                    lines.append(f"• Dağılım orta %50: {cross_venue['lead_q1']:+.1f} ile {cross_venue['lead_q3']:+.1f} saat")
                b=cross_venue["lead_bins"]
                lines.append(f"• Dağılım: Binance ≥6s erken {b['binance_6h_early']} | ±1s yakın {b['near_1h']} | Gate ≥6s geç {b['gate_6h_late']}")
                lines.append(f"• Uç farklar: Binance ≥12s erken {b['binance_12h_early']} | Gate ≥12s geç {b['gate_12h_late']}")
                if cross_venue["lead_n"]<100:
                    lines.append("  → Örneklem hâlâ küçük; medyan 0 olsa bile sonuç 'gecikme yok' değil, 'henüz belirsiz' kabul edilir.")
            labels=(
              ("Binance ortak coinler",cross_venue["binance_earliest"]),
              ("Gate aynı ortak coinler",cross_venue["gate_shared_earliest"]),
              ("Sadece Gate grubu",cross_venue["gate_only_earliest"]),
            )
            for name,row in labels:
                if row:
                    off,feature,eff,nr,nc=row
                    lines.append(f"• {name}: ilk belirgin ayrışma T{off}s | {human_feature(feature)} | etki {fmt(eff)} | n={nr}/{nc}")
            lines.append("  → Amaç: T-24 bulgusunun gerçek piyasa davranışı mı, yoksa Gate gecikmesi mi olduğunu ayırmak.")
            lines.append("  ⚠️ Binance eşleşmesi şu an ticker/sembol bazlıdır; aynı token kimliği kontratla doğrulanana kadar sonuç 'kimlik doğrulanmamış' kabul edilir.")

        if v4_rows:
            lines += ["","🧮 V4 | PATTERN-FIRST TEST"]
            lines.append("• Soru: Bu pattern ÖNCE görülürse, sonradan gerçekten kaç tanesi +50/+100 yapıyor?")
            for name,h,t,n,hits,misses,coins,precision,fpr,recall,base,lift in v4_rows[:6]:
                pname={
                  "P1_FAR_FROM_HIGH":"P1 zirveden uzak",
                  "P2_FAR_PLUS_VOL":"P2 zirveden uzak + oynak",
                  "P3_FAR_VOL_DRAWDOWN":"P3 uzak + oynak + sert geri çekilme",
                }.get(name,name)
                lines.append(
                  f"• {pname} | {h}g +%{t}: başarı %{100*precision:.1f} "
                  f"({hits}/{n}) | boş sinyal %{100*fpr:.1f} | taban %{100*base:.1f} "
                  f"| lift {lift:.2f}x | benzersiz coin {coins}"
                )
            lines.append("  → Burada ilk kez 'kazananlarda pattern var mı?' değil, 'pattern varsa kaç tanesi kazanıyor?' ölçülüyor.")
            lines.append("  → Eşikler sadece keşif dönemindeki feature dağılımından donduruldu; gelecekteki getiriler eşik seçmek için kullanılmadı.")

        if v5_run:
            dtotal,ddone,vtotal,vdone,result_rows,spec_frozen,v5_notes=v5_run
            lines += ["","🚦 V5 | AKTİVASYON TESTİ"]
            lines.append(f"• Saatlik aktivasyon verisi: keşif {ddone}/{dtotal} | doğrulama {vdone}/{vtotal}")
            if not int(spec_frozen or 0):
                lines.append("• Durum: Eşikler henüz dondurulmadı; keşif adaylarının saatlik geçmişi tamamlanıyor.")
                lines.append("  → Sonuca bakarak eşik seçmemek için V5 başarı oranı, keşif verisi tamamlanmadan ana sonuç olarak raporlanmayacak.")
            else:
                if v5_specs:
                    sp={r[0]:float(r[2]) for r in v5_specs}
                    if "vol_ratio_3_24" in sp and "dist_low_24h" in sp:
                        lines.append(f"• Dondurulmuş aktivasyon eşikleri: 3s hacim oranı ≥ {sp['vol_ratio_3_24']:.2f} | 24s dipten uzaklaşma ≥ %{sp['dist_low_24h']:.2f}")
                if v5_rows:
                    lines.append("• Doğrulamada en güçlü aktivasyon sonuçları:")
                    for p,act,h,t,n,hits,coins,prec,fpr,parent,lift_parent,base,lift_raw,pval,qval,fdr in v5_rows[:6]:
                        pname="P2" if p=="P2_FAR_PLUS_VOL" else "P3"
                        aname={"VOL":"hacim","DIP":"dipten ayrışma","VOL_DIP":"hacim + dip"}.get(act,act)
                        fdr_txt="FDR GEÇTİ" if int(fdr or 0) else "FDR GEÇMEDİ"
                        qtxt=f"{float(qval):.3g}" if qval is not None else "—"
                        lines.append(
                          f"  - {pname} + {aname} | {h}g +%{t}: başarı %{100*prec:.1f} "
                          f"({hits}/{n}) | P2/P3 tabanı %{100*parent:.1f} | artış {lift_parent:.2f}x "
                          f"| q={qtxt} {fdr_txt} | coin {coins}"
                        )
                else:
                    lines.append("• Eşikler donduruldu; doğrulama saatlik örnekleri birikiyor.")
            lines.append("  → Cross-Venue/Binance öncülüğü ana V5 hesabına dahil değil; kararsız olduğu için ayrı deneysel katmanda kalıyor.")
            lines.append("  → Çoklu test riski için yalnızca önceden belirlenen 36 ana teste BH-FDR uygulanıyor.")

        lines += ["","📌 METODOLOJİ NOTLARI",
                  "• Eşleştirme kuralı donduruldu; doğrulama setine bakıp eşikler/özellikler ince ayar yapılmayacak.",
                  "• DELIST UYARISI: arşiv hâlâ aktif Gate paritelerinden başlıyor. Ezilip geri dönemeyen/delist olan coinler eksik olabilir; bu yüzden 'ezilmiş coin → patlar' sonucu henüz tam evren sonucu değildir.",
                  "• Delist/suspend geçmişi ayrı backfill ile tamamlanmadan survivorship bias çözülmüş sayılmayacak.",
                  "• Manipülasyon notu: geçmiş holder/wash-trade verisi yoksa organik/manipülatif ayrımı 'bilinmiyor' kalır."]
    chat=resolve_chat_id(token,configured,DB,"History Miner")
    report="\n".join(lines)
    chunks=split_telegram_report(report)
    for idx,chunk in enumerate(chunks,1):
        if len(chunks)>1:
            chunk=f"⛏ GEÇMİŞ KAZICI | BÖLÜM {idx}/{len(chunks)}\n"+chunk
        send_telegram(token,chat,chunk)
    print(f"History research report sent in {len(chunks)} part(s)")

if __name__=="__main__":
    main()
