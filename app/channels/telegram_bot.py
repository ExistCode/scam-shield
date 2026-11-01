"""Telegram Bot API client + CLI.

Local dev without a public URL (long polling):
    .venv/bin/python -m app.channels.telegram_bot poll
Production (Render):
    .venv/bin/python -m app.channels.telegram_bot set-webhook https://<service>.onrender.com/telegram/webhook
    (or set TELEGRAM_WEBHOOK_URL and it's registered on startup)
"""
from __future__ import annotations

import logging
import sys
import threading
from contextlib import contextmanager
from functools import lru_cache

import httpx

from app.agent.schema import Verdict
from app.channels.formatting import guardian_alert_text, to_telegram_html
from app.config import settings

log = logging.getLogger("cekdulu.tg")

MAX_DOWNLOAD = 20 * 1024 * 1024  # Bot API getFile limit


def _api(method: str) -> str:
    return f"https://api.telegram.org/bot{settings.telegram_bot_token}/{method}"


def call(method: str, _timeout: float = 15, **params) -> dict | list | bool:
    if not settings.telegram_bot_token:
        log.warning("TELEGRAM_BOT_TOKEN not set; would call %s %s", method, params)
        return {}
    r = httpx.post(_api(method), json=params, timeout=_timeout)
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code >= 400 or not body.get("ok"):
        # Never log the URL: it contains the bot token.
        raise RuntimeError(f"telegram {method} failed: {r.status_code} {body.get('description', '')}")
    return body["result"]


def send_text(chat_id: int | str, text: str, reply_to: int | None = None) -> None:
    """`text` uses *bold* / _italic_ (same as WhatsApp); converted to safe HTML."""
    params: dict = {
        "chat_id": chat_id,
        "text": to_telegram_html(text)[:4096],
        "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
    }
    if reply_to:
        params["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
    call("sendMessage", **params)


def send_typing(chat_id: int | str) -> None:
    try:
        call("sendChatAction", chat_id=chat_id, action="typing")
    except Exception:
        pass


@contextmanager
def typing(chat_id: int | str, every: float = 4.0):
    """Keep "typing…" visible while we work (Telegram clears it after ~5 s)."""
    stop = threading.Event()

    def loop() -> None:
        while not stop.is_set():
            send_typing(chat_id)
            stop.wait(every)

    threading.Thread(target=loop, daemon=True).start()
    try:
        yield
    finally:
        stop.set()


def send_guardian_alert(chat_id: int | str, parent_label: str, v: Verdict) -> None:
    send_text(chat_id, guardian_alert_text(parent_label, v))


def download_file(file_id: str) -> bytes:
    info = call("getFile", file_id=file_id)
    if int(info.get("file_size") or 0) > MAX_DOWNLOAD:
        raise ValueError("file too large")
    r = httpx.get(
        f"https://api.telegram.org/file/bot{settings.telegram_bot_token}/{info['file_path']}", timeout=30
    )
    if r.status_code >= 400:  # not raise_for_status(): its message would include the token-bearing URL
        raise RuntimeError(f"telegram file download failed: {r.status_code}")
    return r.content


@lru_cache(maxsize=1)
def bot_username() -> str:
    return call("getMe").get("username", "")


def set_webhook(url: str) -> None:
    params: dict = {"url": url, "allowed_updates": ["message"], "drop_pending_updates": False}
    if settings.telegram_webhook_secret:
        params["secret_token"] = settings.telegram_webhook_secret
    call("setWebhook", **params)
    call(
        "setMyCommands",
        commands=[
            {"command": "daftar_wali", "description": "Dapatkan link untuk mendaftarkan wali"},
            {"command": "kata_aman", "description": "Simpan petunjuk kata aman keluarga"},
            {"command": "status", "description": "Lihat wali yang terdaftar"},
            {"command": "hapus_wali", "description": "Hapus semua wali"},
            {"command": "bantuan", "description": "Cara pakai"},
        ],
    )


def poll() -> None:
    """Long-polling loop for local development (no public URL needed)."""
    from app.channels.telegram_webhook import process_update
    from app.db.session import init_db

    init_db()
    call("deleteWebhook")
    log.info("polling as @%s. Forward a message to the bot. Ctrl+C to stop.", bot_username())
    offset = 0
    while True:
        try:
            updates = call("getUpdates", _timeout=40, timeout=30, offset=offset, allowed_updates=["message"])
        except (httpx.HTTPError, RuntimeError) as exc:
            log.warning("getUpdates failed: %s", exc)
            continue
        for u in updates:
            offset = u["update_id"] + 1
            process_update(u)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx logs URLs, which contain the token
    cmd = sys.argv[1] if len(sys.argv) > 1 else "poll"
    if cmd == "poll":
        poll()
    elif cmd == "set-webhook" and len(sys.argv) > 2:
        set_webhook(sys.argv[2])
        print("webhook set:", call("getWebhookInfo").get("url"))
    elif cmd == "info":
        print(call("getWebhookInfo"))
    else:
        print(__doc__)
