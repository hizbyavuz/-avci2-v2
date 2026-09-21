#!/usr/bin/env python3
import html
import sqlite3
from pathlib import Path

DB_FILE = "binance_avci2.db"
OUTPUT_FILE = "binance_dashboard.html"


def esc(value):
    return html.escape("-" if value is None else str(value))


def main():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    latest = conn.execute("SELECT * FROM scans ORDER BY scan_time_utc DESC LIMIT 1").fetchone()
    events = conn.execute(
        """
        SELECT e.signal_time_utc, e.symbol, e.event_class, e.validation_tier,
               e.btc_regime, e.stage, e.engine, e.score, e.spread_bps,
               e.buy_impact_1k_bps, o.label_status, o.net_return_pct,
               o.mfe_pct, o.mae_pct, o.excess_vs_btc_pct,
               o.excess_vs_universe_pct
        FROM signal_events e LEFT JOIN outcome_labels o ON o.event_id=e.event_id
        WHERE e.config_version=(SELECT config_version FROM scans
                                ORDER BY scan_time_utc DESC LIMIT 1)
        ORDER BY e.signal_time_utc DESC LIMIT 100
        """
    ).fetchall()
    headers = list(events[0].keys()) if events else []
    rows = "".join(
        "<tr>" + "".join(f"<td>{esc(row[key])}</td>" for key in headers) + "</tr>"
        for row in events
    )
    page = f"""<!doctype html><html lang='tr'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Binance Avci 2</title><style>
body{{font-family:system-ui;background:#0d1117;color:#e6edf3;margin:20px}}
.cards{{display:flex;gap:12px;flex-wrap:wrap}} .card{{background:#161b22;padding:14px;border-radius:10px}}
table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{padding:8px;border-bottom:1px solid #30363d;text-align:left}}
th{{position:sticky;top:0;background:#161b22}} .scroll{{overflow:auto;max-height:70vh}}
</style></head><body><h1>Binance Avci 2</h1>
<div class='cards'><div class='card'>Son tarama<br><b>{esc(latest['scan_time_utc'] if latest else None)}</b></div>
<div class='card'>Veri sağlığı<br><b>{esc(latest['health_status'] if latest else None)}</b></div>
<div class='card'>BTC rejimi<br><b>{esc(latest['btc_regime'] if latest else None)}</b></div>
<div class='card'>Evren<br><b>{esc(latest['universe_size'] if latest else None)}</b></div></div>
<h2>Son olaylar</h2><div class='scroll'><table><thead><tr>{''.join(f'<th>{esc(h)}</th>' for h in headers)}</tr></thead>
<tbody>{rows}</tbody></table></div>
<p>PRIMARY: tam veri doğrulaması. OBSERVATIONAL: eksik dış veriyle gözlem.</p></body></html>"""
    Path(OUTPUT_FILE).write_text(page, encoding="utf-8")
    print(f"Dashboard written: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
