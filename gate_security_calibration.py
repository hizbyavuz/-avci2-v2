"""Historical calibration report for Gate security-confidence labels.

Reads only already-recorded observations/outcomes. No scoring or frozen rules
are changed by this report.
"""
import sqlite3

OBS="avci2.db"
VAL="avci_validation_v5.db"

def main(obs_path=OBS,val_path=VAL):
    with sqlite3.connect(obs_path) as obs:
        exists=obs.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='gate_security_confidence_history'").fetchone()
        if not exists:
            print("Güvenlik güveni kalibrasyonu: henüz geçmiş yok")
            return
        rows=obs.execute("""SELECT validation_event_id,label,coverage_pct,hard_veto
            FROM gate_security_confidence_history
            WHERE validation_event_id IS NOT NULL""").fetchall()
    if not rows:
        print("Güvenlik güveni kalibrasyonu: validation eşleşmesi henüz yok")
        return
    with sqlite3.connect(val_path) as val:
        print("Güvenlik güveni geçmiş karşılaştırması (gözlemsel):")
        for label in ("STRONG","MEDIUM","WEAK","BLOCKED"):
            ids=[r[0] for r in rows if r[1]==label]
            if not ids:
                continue
            marks=",".join("?" for _ in ids)
            events=val.execute(f"""SELECT status,hit_3_ts,hit_5_ts,hit_10_ts,stop_ts,
                net_final_pct FROM validation_events WHERE id IN ({marks})""",ids).fetchall()
            closed=[e for e in events if e[0] not in ("WAIT_ENTRY","OPEN")]
            def rate(idx):
                return (100*sum(e[idx] is not None for e in closed)/len(closed)) if closed else None
            r3=rate(1); r5=rate(2); r10=rate(3); stop=rate(4)
            avg=(sum(float(e[5]) for e in closed if e[5] is not None)/
                 sum(e[5] is not None for e in closed)) if any(e[5] is not None for e in closed) else None
            print(f"  {label}: n={len(events)} closed={len(closed)}"
                  + (f" | +3 %{r3:.1f} +5 %{r5:.1f} +10 %{r10:.1f} stop %{stop:.1f}" if closed else "")
                  + (f" | avg net %{avg:+.2f}" if avg is not None else ""))
        print("Not: küçük örneklemde bu oranlar karar kuralı değildir; yalnız kalibrasyon içindir.")

if __name__=="__main__":
    main()
