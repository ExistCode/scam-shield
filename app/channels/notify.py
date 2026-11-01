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


def short_name(user: User, profile_contacts: list[dict] | None = None) -> str:
    """How the parent sees a guardian: the name in their profile, else their name, else a masked number."""
    for c in profile_contacts or []:
        if c.get("user_id") == user.id and c.get("name"):
            return c["name"]
    if user.name:
        return user.name.split(" (@")[0]
    if user.wa_number:
        n = user.wa_number
        return "+" + n[:4] + "****" + n[-3:] if len(n) > 7 else "+" + n
    return "wali (Telegram)"


def alert_guardians(parent_user_id: int, parent_label: str, verdict: Verdict) -> tuple[str, list[str]]:
    """Alert the parent's guardians for a verdict. Returns (status, names actually alerted) so the reply
    can say exactly what happened.
    status: "sent" | "already" (cooldown) | "failed" | "none" (no guardian) | "" (not BAHAYA)."""
    from app.agent import guardian_policy
    from app.db import repo

    if verdict.risk != "BAHAYA":
        return "", []
    guardians = repo.active_guardians(parent_user_id)
    if not guardians:
        return "none", []
    fam = repo.get_profile(parent_user_id)["contacts"]
    targets = guardian_policy.guardians_to_alert(parent_user_id, verdict)
    if not targets:
        return "already", [short_name(g, fam) for g in guardians]
    names = [short_name(g, fam) for g in targets if alert_guardian(g, parent_label, verdict)]
    return ("sent", names) if names else ("failed", [short_name(g, fam) for g in targets])


def describe(user: User) -> str:
    if user.wa_number:
        return f"+{user.wa_number}" + (f" ({user.name})" if user.name else "")
    return f"{user.name or 'Pengguna'} (Telegram)"
