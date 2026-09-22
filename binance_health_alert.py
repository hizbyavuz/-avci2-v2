"""Best-effort failure notification for a failed GitHub Actions scan job."""

import json
import os
import urllib.parse
import urllib.request


def telegram_call(token, method, payload):
    data = urllib.parse.urlencode(payload).encode()
    with urllib.request.urlopen(
        urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=data),
        timeout=12,
    ) as response:
        return json.load(response)


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("Tarama hata bildirimi için Telegram bot token yok")
        return
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not chat_id:
        updates = telegram_call(token, "getUpdates", {})
        private = [item["message"]["chat"]["id"]
                   for item in updates.get("result", [])
                   if item.get("message", {}).get("chat", {}).get("type") == "private"]
        if not private:
            print("Tarama hata bildirimi için Telegram sohbeti bulunamadı")
            return
        chat_id = private[-1]
    repo = os.getenv("GITHUB_REPOSITORY", "")
    run_id = os.getenv("GITHUB_RUN_ID", "")
    message = ("⚠️ BINANCE AVCI 2 ÇALIŞMA HATASI\n"
               f"Kod: {os.getenv('GITHUB_SHA', '-')[:12]}\n"
               f"Çalışma: https://github.com/{repo}/actions/runs/{run_id}\n"
               "Tarama veya rapor tamamlanmadı; bu turdaki aday kararı güvenilir değil.")
    result = telegram_call(token, "sendMessage", {"chat_id": chat_id, "text": message})
    if not result.get("ok"):
        raise RuntimeError("Telegram hata bildirimi reddedildi")
    print("GitHub Actions hata bildirimi gönderildi")


if __name__ == "__main__":
    main()
