#!/usr/bin/env python3
import argparse
from datetime import datetime, timezone

from binance_snapshot_store import init_db, save_manual_trade


def main():
    parser = argparse.ArgumentParser(description="Manual trade journal")
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--action", required=True, choices=("ENTRY", "EXIT", "SKIP"))
    parser.add_argument("--price", type=float, default=0.0)
    parser.add_argument("--quantity", type=float)
    parser.add_argument("--fee", type=float, default=0.0)
    parser.add_argument("--execution-time")
    parser.add_argument("--decision-time")
    parser.add_argument("--notes", default="")
    args = parser.parse_args()
    execution_time = args.execution_time or datetime.now(timezone.utc).isoformat()
    init_db()
    save_manual_trade(
        args.event_id, args.action, execution_time, args.price,
        args.quantity, args.fee, args.notes, args.decision_time,
    )
    print(f"Journal saved: {args.event_id} {args.action}")


if __name__ == "__main__":
    main()
