#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, sqlite3, requests

DB=os.getenv("HISTORY_DB","history_miner.db")
VERSION="history-twin-v0.1-20260925"

def send(text):
    token=os.getenv("TELEGRAM_BOT_TOKEN")
    chat=os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print(text); return
    r=requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
      json={"chat_id":chat,"text":text,"disable_web_page_preview":True},timeout=25)
    r.raise_for_status()

def fmt(x):
    if x is None:return "—"
    return f"{float(x):.2f}"

def main():
    if not os.path.exists(DB):
        print("Twin Telegram: DB yok"); return
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
    run=c.execute("""SELECT matches,result_rows,notes FROM twin_runs
      WHERE version=? ORDER BY started_utc DESC LIMIT 1""",(VERSION,)).fetchone()
    if not run:
        print("Twin Telegram: henüz run yok"); return

    counts=c.execute("""SELECT split,pattern_name,target_pct,COUNT(*) n
      FROM twin_matches WHERE version=?
      GROUP BY split,pattern_name,target_pct ORDER BY split,pattern_name,target_pct""",
      (VERSION,)).fetchall()

    disc={(r["pattern_name"],int(r["target_pct"]),r["feature"]):r for r in c.execute(
      """SELECT * FROM twin_results WHERE version=? AND split='DISCOVERY'""",(VERSION,))}
    val=list(c.execute("""SELECT * FROM twin_results
      WHERE version=? AND split='VALIDATION' AND pair_n>=20
      ORDER BY ABS(paired_effect) DESC""",(VERSION,)))

    names={
      "ret_1d":"1 günlük fiyat değişimi",
      "ret_3d":"3 günlük fiyat değişimi",
      "vol_accel_3v7":"3 günlük hacim hızlanması",
      "range_accel_3v7":"hareket genişliği hızlanması",
      "green_ratio_3d":"son 3 gün yeşil gün oranı",
      "dist_low_7d":"7 günlük dipten uzaklaşma",
    }
    lines=["🧬 İKİZ TESTİ | WINNER vs NEAR-MISS",
      "• Soru: Birbirine çok benzeyen iki coinden biri patlarken diğeri neden patlamadı?",
      f"• Toplam eşleşmiş çift: {int(run['matches'])}",
      "• Eşleştirme: aynı P2/P3 tipi + aynı BTC rejimi (biliniyorsa) + ±45 gün + benzer ezilme/oynaklık/geri çekilme.",
      "• Gelecek bilgisi özelliklere sokulmuyor; yalnızca sinyalden önce bilinen veriler kullanılıyor."]

    if counts:
        lines.append("• Çift sayıları:")
        for r in counts:
            p="P2" if r["pattern_name"]=="P2_FAR_PLUS_VOL" else "P3"
            lines.append(f"  - {r['split']} {p} +%{r['target_pct']}: {r['n']} çift")

    stable=[]
    for r in val:
        k=(r["pattern_name"],int(r["target_pct"]),r["feature"])
        d=disc.get(k)
        if not d or d["paired_effect"] is None or r["paired_effect"] is None: continue
        if float(d["paired_effect"])*float(r["paired_effect"])<=0: continue
        stable.append(r)

    lines.append("")
    lines.append("🔍 ŞİMDİLİK AYIRT EDİCİ FARKLAR")
    if not stable:
        lines.append("• Henüz keşif ve doğrulamada aynı yönü koruyan, yeterli çift sayılı fark yok.")
    else:
        for r in stable[:6]:
            p="P2" if r["pattern_name"]=="P2_FAR_PLUS_VOL" else "P3"
            direction="kazananlarda daha yüksek" if float(r["paired_effect"])>0 else "kazananlarda daha düşük"
            lines.append(
              f"• {p} +%{r['target_pct']} | {names.get(r['feature'],r['feature'])}: {direction} "
              f"| etki {fmt(r['paired_effect'])} | çift {r['pair_n']} | yön uyumu %{r['same_direction']}"
            )
            lines.append(f"  → kazanan medyan {fmt(r['winner_median'])} | ikizi {fmt(r['near_median'])}")

    lines += ["",
      "⚠️ Bu ayrı araştırma katmanıdır; V5'i veya dondurulmuş eski testleri değiştirmez.",
      "⚠️ Delist/rug geçmişi ve sahte hacim ayrımı tam olmadığı için sonuçlar henüz canlı alım kuralı değildir.",
      "• Amaç: genel 'ezilmiş coin' bilgisinden çıkıp, patlayan ile patlamayan ikiz arasındaki SON farkı bulmak."]

    send("\n".join(lines)[:3900])
    c.close()

if __name__=="__main__":
    main()
