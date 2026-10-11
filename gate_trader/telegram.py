"""Gate Trader Telegram transport. Uses the existing bot and chat."""
from __future__ import annotations
import os
import requests

_chat_cache = None

def _chat(token):
    global _chat_cache
    configured = (os.getenv("GT_TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if configured:
        return configured
    if _chat_cache:
        return _chat_cache
    try:
        response = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=15)
        response.raise_for_status()
        for update in reversed(response.json().get("result") or []):
            message = update.get("message") or update.get("channel_post") or {}
            chat_id = (message.get("chat") or {}).get("id")
            if chat_id:
                _chat_cache = str(chat_id)
                return _chat_cache
    except Exception as exc:
        print("TELEGRAM_CHAT_DISCOVERY_ERROR", type(exc).__name__, flush=True)
    return None

def send(message):
    token = (os.getenv("GT_TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        print("TELEGRAM_TOKEN_MISSING", flush=True)
        return False
    chat_id = _chat(token)
    if not chat_id:
        print("TELEGRAM_CHAT_ID_MISSING: send /start to existing bot or set TELEGRAM_CHAT_ID", flush=True)
        return False
    try:
        response = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "disable_web_page_preview": True},
            timeout=15)
        if not response.ok:
            print("TELEGRAM_SEND_FAILED", response.status_code, flush=True)
            return False
        return bool(response.json().get("ok"))
    except Exception as exc:
        print("TELEGRAM_SEND_ERROR", type(exc).__name__, flush=True)
        return False
