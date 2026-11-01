"""WA sender: text replies and the guardian_alert utility template via Graph API /messages."""
import logging

import httpx

from app.agent.schema import Verdict
from app.channels.formatting import alert_phrase, guardian_alert_text
from app.config import settings

log = logging.getLogger("cekdulu.wa")


def _post(payload: dict) -> dict:
    if not (settings.wa_access_token and settings.wa_phone_number_id):
        log.warning("WA not configured; would send: %s", payload)
        return {"skipped": True}
    r = httpx.post(
        f"{settings.graph_base}/{settings.wa_phone_number_id}/messages",
        headers={"Authorization": f"Bearer {settings.wa_access_token}"},
        json={"messaging_product": "whatsapp", **payload},
        timeout=15,
    )
    if r.status_code >= 400:
        log.error("WA send failed %s: %s", r.status_code, r.text[:500])
        r.raise_for_status()
    return r.json()


def send_text(to: str, body: str, reply_to: str | None = None) -> dict:
    payload: dict = {"to": to, "type": "text", "text": {"body": body[:4096], "preview_url": False}}
    if reply_to:
        payload["context"] = {"message_id": reply_to}
    return _post(payload)


def mark_read(message_id: str) -> None:
    try:
        _post({"status": "read", "message_id": message_id})
    except Exception:  # cosmetic only
        pass


def send_guardian_alert(guardian_number: str, guardian: str, panggilan: str, v: Verdict) -> dict:
    """Utility template; falls back to free-form text (works inside the 24h window).

    Template body expected (4 params, see README):
      "🔴 {{1}}, {{2}} barusan dapet {{3}}. Kayaknya penipuan. Telepon {{4}} sekarang ya. - Ronda"
    The "already paid" emergency text has no template and is sent free-form.
    """
    if v.money_sent:
        return send_text(guardian_number, guardian_alert_text(guardian, panggilan, v))
    params = [guardian or "Halo", panggilan or "keluargamu", alert_phrase(v), panggilan or "keluargamu"]
    try:
        return _post(
            {
                "to": guardian_number,
                "type": "template",
                "template": {
                    "name": settings.wa_guardian_template,
                    "language": {"code": settings.wa_template_lang},
                    "components": [
                        {
                            "type": "body",
                            "parameters": [{"type": "text", "text": t[:200]} for t in params],
                        }
                    ],
                },
            }
        )
    except httpx.HTTPError as exc:
        log.warning("template send failed (%s); falling back to free-form text", exc)
        body = guardian_alert_text(guardian, panggilan, v)
        return send_text(guardian_number, body)
