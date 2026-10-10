"""Offline contract checks. Does not send messages or trade."""
from datetime import datetime

def gross_pct(direction, entry, exit_price):
    if entry <= 0 or exit_price <= 0:
        raise ValueError("bad price")
    if direction == "LONG":
        return 100 * (exit_price - entry) / entry
    if direction == "SHORT":
        return 100 * (entry - exit_price) / entry
    raise ValueError("bad direction")

def net_pct(direction, entry, exit_price, cost_bps=30):
    return gross_pct(direction, entry, exit_price) - cost_bps / 100

def first_barrier(direction, high, low, stop, target):
    if direction == "LONG":
        stop_hit, target_hit = low <= stop, high >= target
    elif direction == "SHORT":
        stop_hit, target_hit = high >= stop, low <= target
    else:
        raise ValueError("bad direction")
    return "STOP" if stop_hit else "TARGET" if target_hit else "NONE"

def telegram_receipt(expected_chat_id, resolved_chat_id, response):
    if str(expected_chat_id) != str(resolved_chat_id):
        return "CHAT_MISMATCH"
    if not response.get("ok"):
        return "API_ERROR"
    result = response.get("result") or {}
    if str((result.get("chat") or {}).get("id")) != str(expected_chat_id):
        return "RESPONSE_CHAT_MISMATCH"
    if not result.get("message_id") or result.get("date") is None:
        return "MISSING_RECEIPT"
    return "RECEIPT_OK"
