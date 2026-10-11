import os
VERSION="GATE_TRADER_V1_1_RAM_2026-10-12"
MAX_COINS=int(os.getenv("GT_MAX_COINS","40"))
SCAN_DELAY_SEC=int(os.getenv("GT_SCAN_DELAY_SEC","20"))
TRACK_EVERY_SEC=int(os.getenv("GT_TRACK_EVERY_SEC","60"))
RISK_USD=float(os.getenv("GT_RISK_USD","10"))
MIN_RR=2.0
