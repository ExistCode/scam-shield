"""Guardian alert dispatch: send on whichever channel the guardian is reachable on."""
import logging

from app.agent.schema import Verdict
from app.db.models import User

log = logging.getLogger("cekdulu.notify")


def alert_guardian(guardian: User, parent_label: str, verdict: Verdict) -> bool:
    """Returns True if the alert was handed to a channel."""
    from app.channels import telegram_bot, whatsapp_sender  # local import: avoid cycles

    try:
        if guardian.tg_chat_id:
            telegram_bot.send_guardian_alert(guardian.tg_chat_id, parent_label, verdict)
            return True
        if guardian.wa_number:
            whatsapp_sender.send_guardian_alert(guardian.wa_number, parent_label, verdict)
            return True
    except Exception as exc:
        log.error("guardian alert to user %s failed: %s", guardian.id, exc)
    return False


def describe(user: User) -> str:
    if user.wa_number:
        return f"+{user.wa_number}" + (f" ({user.name})" if user.name else "")
    return f"{user.name or 'Pengguna'} (Telegram)"
