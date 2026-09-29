#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Plain 3-day Telegram scorecard. Reporting only; frozen rules stay untouched."""
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from avci_monthly_report import latest_backup
from avci_state_guard import download, valid_database
from binance_notify import get_telegram_settings, send_telegram

def read_closed(path, source, since):
    if not valid_database(path, "trader_label_ledger"):
        return []
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as c:
        c.row_factory=sqlite3.Row
        return c.execute("""SELECT display_name,label,mfe_pct,mae_pct,final_return_pct,
                 signal_time_utc,closed_at_utc
            FROM trader_label_ledger
            WHERE source=? AND status='CLOSED' AND closed_at_utc>=?
            ORDER BY closed_at_utc DESC""",(source,since.isoformat())).fetchall()

def classify(r):
    mfe=r["mfe_pct"]
    final=r["final_return_pct"]
    if mfe is None and final is None:
        return "DATA"
    m=float(mfe or 0); f=float(final or 0)
    if m>=10: return "WIN"
    if m>=5: return "PARTIAL"
    if f<=-7: return "LOSS"
    return "WEAK"

def venue_lines(name, rows):
    measured=[r for r in rows if classify(r)!="DATA"]
    wins=[r for r in measured if classify(r)=="WIN"]
    partial=[r for r in measured if classify(r)=="PARTIAL"]
    losses=[r for r in measured if classify(r)=="LOSS"]
    weak=[r for r in measured if classify(r)=="WEAK"]
    lines=[f"📍 {name}", f"• Son 3 günde kapanan sinyal: {len(rows)} | ölçülebilen: {len(measured)}"]
    if not measured:
        lines.append("• Henüz yorum yapacak yeni kapanmış sinyal yok.")
        return lines
    lines.append(f"• +%10 gören: {len(wins)}/{len(measured)} | +%5–10 gören: {len(partial)}/{len(measured)}")
    lines.append(f"• Devam gelmeyen/zayıf: {len(weak)} | sert başarısız: {len(losses)}")
    rate=100*len(wins)/len(measured)
    if len(measured)<20:
        lines.append(f"• Şimdilik +%10 oranı: %{rate:.0f} — örnek az, kesin sonuç değil.")
    else:
        lines.append(f"• +%10 oranı: %{rate:.0f} (n={len(measured)})")
    for r in measured[:5]:
        cls=classify(r)
        icon={"WIN":"🟢","PARTIAL":"🟡","LOSS":"🔴","WEAK":"⚪️"}[cls]
        label={"WIN":"başarılı","PARTIAL":"kısmi başarı","LOSS":"başarısız","WEAK":"devam zayıf"}[cls]
        mfe="-" if r["mfe_pct"] is None else f"%{float(r['mfe_pct']):+.1f}"
        final="-" if r["final_return_pct"] is None else f"%{float(r['final_return_pct']):+.1f}"
        lines.append(f"{icon} {r['display_name']} — {label} | en iyi {mfe} | 72s sonu {final}")
    if len(measured)>5:
        lines.append(f"• +{len(measured)-5} sonuç daha veritabanında kayıtlı.")
    return lines

def main():
    out=Path(".three-day-scorecard")
    out.mkdir(exist_ok=True)
    bdir=out/"binance"; gdir=out/"gate"
    bdir.mkdir(exist_ok=True); gdir.mkdir(exist_ok=True)
    download(latest_backup("binance-avci2-state","binance"),bdir)
    download(latest_backup("gate-avci2-signal-history","gate"),gdir)
    bdb=bdir/"binance_avci2.db"
    gdb=gdir/"avci2.db"
    since=datetime.now(timezone.utc)-timedelta(days=3)
    b=read_closed(bdb,"BINANCE",since)
    g=read_closed(gdb,"GATE",since)
    lines=["📊 AVCI — 3 GÜNLÜK KISA KARNE",
           "Bu rapor sadece Telegram'da gerçekten gösterilmiş sinyallerin sonradan ne yaptığını özetler.", ""]
    lines.extend(venue_lines("BINANCE",b))
    lines.append("")
    lines.extend(venue_lines("GATE WEB3",g))
    lines.extend(["", "Nasıl okunur:",
                  "🟢 = sinyalden sonra +%10 gördü",
                  "🟡 = +%5 gördü ama +%10'a ulaşmadı",
                  "⚪️ = anlamlı devam gelmedi",
                  "🔴 = 72 saat sonunda sert negatif kaldı", "",
                  "Sinyal öncesindeki yükseliş başarı sayılmaz. Bu karne frozen kuralları değiştirmez."])
    msg="\n".join(lines)[:3900]
    print(msg)
    token,chat=get_telegram_settings()
    if token and chat:
        send_telegram(token,chat,msg)

if __name__=="__main__":
    main()
