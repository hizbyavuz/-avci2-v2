"""Compact observational QA summary for GitHub job summaries."""
import json, os, sqlite3, sys
from collections import Counter

def summarize_binance(path="binance_avci2.db"):
    con=sqlite3.connect(path); con.row_factory=sqlite3.Row
    try:
        rows=con.execute("""SELECT symbol,miss_reason_json,flags_json FROM opportunity_observations
            WHERE scan_time_utc=(SELECT MAX(scan_time_utc) FROM opportunity_observations)
            AND missed_mover=1""").fetchall() if con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='opportunity_observations'").fetchone() else []
        reasons=Counter(x for r in rows for x in json.loads(r["miss_reason_json"] or "[]"))
        cp=con.execute("SELECT horizon,COUNT(*) FROM signal_checkpoints GROUP BY horizon").fetchall() if con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='signal_checkpoints'").fetchone() else []
        return ["## Binance observational QA",f"Missed mover: {len(rows)}",
                "Nedenler: "+(", ".join(f"{k}={v}" for k,v in reasons.most_common()) or "yok"),
                "Checkpoint kayıtları: "+(", ".join(f"{a}={b}" for a,b in cp) or "henüz yok")]
    finally: con.close()

def summarize_gate(path="avci2.db"):
    con=sqlite3.connect(path); con.row_factory=sqlite3.Row
    try:
        rows=con.execute("""SELECT pair,miss_reason_json FROM gate_opportunity_observations
            WHERE batch_id=(SELECT MAX(batch_id) FROM gate_opportunity_observations)
            AND missed_mover=1""").fetchall() if con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='gate_opportunity_observations'").fetchone() else []
        reasons=Counter(x for r in rows for x in json.loads(r["miss_reason_json"] or "[]"))
        cp=con.execute("SELECT horizon,COUNT(*) FROM gate_signal_checkpoints GROUP BY horizon").fetchall() if con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='gate_signal_checkpoints'").fetchone() else []
        return ["## Gate observational QA",f"Missed mover: {len(rows)}",
                "Nedenler: "+(", ".join(f"{k}={v}" for k,v in reasons.most_common()) or "yok"),
                "Checkpoint kayıtları: "+(", ".join(f"{a}={b}" for a,b in cp) or "henüz yok")]
    finally: con.close()

if __name__=="__main__":
    lines=summarize_binance() if sys.argv[1]=="binance" else summarize_gate()
    text="\n\n".join(lines)+"\n"
    print(text)
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"],"a",encoding="utf-8") as f: f.write("\n"+text)
