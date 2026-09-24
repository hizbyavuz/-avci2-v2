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

def fmt(x):
    if x is None: return "—"
    ax=abs(x)
    if ax>=1000: return f"{x:,.0f}"
    if ax>=10: return f"{x:.1f}"
    return f"{x:.2f}"

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
    attempted,ok,bars,events,controls=run
    pairs,done,total_bars,total_events,total_controls=total
    lines=[
      "⛏ GEÇMİŞ KAZICI | SON TUR",
      f"• Bu tur taranan parite: {attempted} (başarılı {ok})",
      f"• Bu tur işlenen günlük mum: {bars:,}",
      f"• Bu tur bulunan yükseliş olayı: {events}",
      f"• Bu tur bulunan kontrol dönemi: {controls}",
      "",
      "📚 TOPLAM ARŞİV",
      f"• Geçmişi işlenen parite: {done}/{pairs}",
      f"• Günlük mum: {total_bars:,}",
      f"• Yükseliş olayı: {total_events}",
      f"• Kontrol dönemi: {total_controls}",
    ]
    if diffs:
        lines += ["","🔎 ŞİMDİLİK EN BELİRGİN FARKLAR"]
        for _score,label,direction,rm,cm,nr,nc in diffs:
            lines.append(f"• {label}: yükselişlerde {direction} (medyan {fmt(rm)} vs {fmt(cm)})")
        lines.append("")
        lines.append("Not: Bunlar henüz korelasyon. Örneklem büyüdükçe kalıcı mı, tesadüf mü göreceğiz.")
    else:
        lines += ["","🔎 BENZERLİK","• Henüz karşılaştırma için yeterli yükseliş/kontrol örneği yok."]

    if activation_diffs:
        lines += ["","⚡ UYANIŞ İZLERİ | PATLAYAN vs PATLAMAYAN"]
        for _score,label,direction,rm,cm,nr,nc in activation_diffs:
            lines.append(f"• {label}: patlayanlarda {direction} (medyan {fmt(rm)} vs {fmt(cm)})")
        lines += ["","Yorum: Burada aradığımız şey 'düşmüş coin' değil; düşmüşken içeride aktivitesi değişmeye başlayan coin."]
    chat=resolve_chat_id(token,configured,DB,"History Miner")
    send_telegram(token,chat,"\n".join(lines))
    print("History research report sent")

if __name__=="__main__":
    main()
