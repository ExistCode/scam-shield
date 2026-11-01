"""Small persistence helpers shared by the WhatsApp and web entry points."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.agent.orchestrator import details
from app.agent.schema import AnalysisResult
from app.db.models import Case, GuardianLink, ReportedNumber, User, UserProfile, VerdictRow
from app.db.session import get_session


def normalize_number(raw: str) -> str | None:
    """Indonesian-friendly E.164 digits without '+': 0812.. / +62812.. / 812.. -> 62812.."""
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("0"):
        digits = "62" + digits[1:]
    elif digits.startswith("8"):
        digits = "62" + digits
    return digits if 10 <= len(digits) <= 15 else None


def upsert_user(wa_number: str, name: str | None = None, role: str = "parent") -> tuple[User, bool]:
    """Returns (user, is_new)."""
    with get_session() as s:
        user = s.scalar(select(User).where(User.wa_number == wa_number))
        if user:
            if name and not user.name:
                user.name = name
            return user, False
        user = User(wa_number=wa_number, name=name, role=role)
        s.add(user)
        s.flush()
        return user, True


def upsert_tg_user(chat_id: int | str, name: str | None = None, role: str = "parent") -> tuple[User, bool]:
    """Telegram identity = private chat id. Returns (user, is_new)."""
    chat_id = str(chat_id)
    with get_session() as s:
        user = s.scalar(select(User).where(User.tg_chat_id == chat_id))
        if user:
            if name and not user.name:
                user.name = name
            return user, False
        user = User(tg_chat_id=chat_id, name=name, role=role)
        s.add(user)
        s.flush()
        return user, True


def upsert_web_user(web_id: str, name: str | None = None, role: str = "parent") -> tuple[User, bool]:
    """Simulated chat user of the /wa demo page. Returns (user, is_new)."""
    with get_session() as s:
        user = s.scalar(select(User).where(User.web_id == web_id))
        if user:
            if name and not user.name:
                user.name = name
            return user, False
        user = User(web_id=web_id, name=name, role=role)
        s.add(user)
        s.flush()
        return user, True


def get_user(user_id: int) -> User | None:
    with get_session() as s:
        return s.get(User, user_id)


def set_consent(user_id: int) -> None:
    with get_session() as s:
        user = s.get(User, user_id)
        if user and not user.consent_at:
            user.consent_at = datetime.now(timezone.utc)


def create_case(user_id: int | None, channel: str, input_type: str, input_text: str | None,
                channel_message_id: str | None = None) -> int | None:
    """Returns case id, or None if this channel message id was already processed (dedupe)."""
    try:
        with get_session() as s:
            case = Case(user_id=user_id, channel=channel, input_type=input_type,
                        input_text=(input_text or "")[:4000], channel_message_id=channel_message_id)
            s.add(case)
            s.flush()
            return case.id
    except IntegrityError:
        return None


def save_verdict(case_id: int, result: AnalysisResult, input_text: str | None = None) -> None:
    v = result.verdict
    with get_session() as s:
        if input_text is not None:
            case = s.get(Case, case_id)
            if case:
                case.input_text = input_text[:4000]
        s.add(
            VerdictRow(
                case_id=case_id, risk=v.risk, category=v.category, confidence=v.confidence,
                reason=v.reason, next_steps=v.next_steps, tools_used=result.tools_used,
                model=result.model or None, provider=result.provider or None,
                latency_ms=result.latency_ms, guardian_alerted=False, details=details(result),
            )
        )
        if v.risk != "AMAN":
            case = s.get(Case, case_id)
            for kind, nums in (("phone", v.scam_phone_numbers), ("account", v.scam_account_numbers)):
                for n in nums:
                    s.add(ReportedNumber(kind=kind, number=re.sub(r"\D", "", n), risk=v.risk, case_id=case_id,
                                         user_id=case.user_id if case else None))


def last_case_for(user_id: int, within: timedelta) -> dict | None:
    """Most recent case + verdict for this user, if newer than `within`."""
    since = datetime.now(timezone.utc) - within
    with get_session() as s:
        row = s.execute(
            select(Case, VerdictRow)
            .join(VerdictRow, VerdictRow.case_id == Case.id)
            .where(Case.user_id == user_id, Case.created_at >= since)
            .order_by(Case.id.desc())
            .limit(1)
        ).first()
        if not row:
            return None
        case, v = row
        return {"case_id": case.id, "input_text": case.input_text, "risk": v.risk, "category": v.category,
                "reason": v.reason, "next_steps": v.next_steps or [],
                "scam_type": (v.details or {}).get("scam_type")}


def mark_alerted(case_id: int) -> None:
    with get_session() as s:
        row = s.scalar(select(VerdictRow).where(VerdictRow.case_id == case_id))
        if row:
            row.guardian_alerted = True


def link_guardian(parent_id: int, guardian_number: str) -> User:
    """WhatsApp: link a guardian by phone number."""
    guardian, _ = upsert_user(guardian_number, role="guardian")
    link_guardian_user(parent_id, guardian.id)
    return guardian


def link_guardian_user(parent_id: int, guardian_id: int) -> None:
    with get_session() as s:
        guardian = s.get(User, guardian_id)
        existing = s.scalar(
            select(GuardianLink).where(
                GuardianLink.parent_user_id == parent_id, GuardianLink.guardian_user_id == guardian_id
            )
        )
        if existing:
            existing.active = True
        else:
            s.add(GuardianLink(parent_user_id=parent_id, guardian_user_id=guardian_id))
        if guardian and guardian.role != "guardian":
            guardian.role = "guardian"


def unlink_guardians(parent_id: int) -> int:
    with get_session() as s:
        links = s.scalars(
            select(GuardianLink).where(GuardianLink.parent_user_id == parent_id, GuardianLink.active.is_(True))
        ).all()
        for l in links:
            l.active = False
        return len(links)


def active_guardians(parent_id: int) -> list[User]:
    with get_session() as s:
        return list(
            s.scalars(
                select(User)
                .join(GuardianLink, GuardianLink.guardian_user_id == User.id)
                .where(GuardianLink.parent_user_id == parent_id, GuardianLink.active.is_(True))
            ).all()
        )


def set_safe_word_hint(parent_id: int, hint: str) -> int:
    with get_session() as s:
        links = s.scalars(
            select(GuardianLink).where(GuardianLink.parent_user_id == parent_id, GuardianLink.active.is_(True))
        ).all()
        for l in links:
            l.safe_word_hint = hint[:200]
        return len(links)


# --------------------------------------------------------------------------- profile (persona)

def _profile_row(s, user_id: int) -> UserProfile:
    row = s.get(UserProfile, user_id)
    if row is None:
        row = UserProfile(user_id=user_id, bahasa_style="santai", contacts=[])
        s.add(row)
        s.flush()
    return row


def get_profile(user_id: int | None) -> dict:
    """{"panggilan", "bahasa_style", "contacts": [{"name", "relation"}], "awaiting"} (defaults if none)."""
    if user_id is None:
        return {"panggilan": None, "bahasa_style": "santai", "contacts": [], "awaiting": None}
    with get_session() as s:
        row = s.get(UserProfile, user_id)
        if row is None:
            return {"panggilan": None, "bahasa_style": "santai", "contacts": [], "awaiting": None}
        return {"panggilan": row.panggilan, "bahasa_style": row.bahasa_style or "santai",
                "contacts": list(row.contacts or []), "awaiting": row.awaiting}


def set_panggilan(user_id: int, panggilan: str) -> None:
    with get_session() as s:
        row = _profile_row(s, user_id)
        row.panggilan = panggilan[:40]
        row.awaiting = None


def set_style(user_id: int, style: str) -> None:
    with get_session() as s:
        _profile_row(s, user_id).bahasa_style = "sopan" if style == "sopan" else "santai"


def set_awaiting(user_id: int, what: str | None) -> None:
    with get_session() as s:
        _profile_row(s, user_id).awaiting = what


def add_profile_contact(parent_id: int, name: str, relation: str, guardian_user_id: int | None = None) -> None:
    """Called when a guardian links: they become a named family contact in the parent's profile."""
    with get_session() as s:
        row = _profile_row(s, parent_id)
        contacts = [c for c in (row.contacts or [])
                    if not (guardian_user_id and c.get("user_id") == guardian_user_id)]
        contacts.append({"name": name[:40], "relation": relation[:20], "user_id": guardian_user_id})
        row.contacts = contacts  # reassign so the JSON change is persisted


def clear_profile_contacts(parent_id: int) -> None:
    with get_session() as s:
        row = s.get(UserProfile, parent_id)
        if row:
            row.contacts = []


def count_reports(kind: str, number: str, exclude_user_id: int | None = None) -> int:
    """How many *other* Cek Dulu users got this number in a WASPADA/BAHAYA check (web test page excluded)."""
    digits = re.sub(r"\D", "", number)
    with get_session() as s:
        q = select(func.count(func.distinct(ReportedNumber.user_id))).where(
            ReportedNumber.kind == kind, ReportedNumber.number == digits, ReportedNumber.user_id.is_not(None))
        if exclude_user_id is not None:
            q = q.where(ReportedNumber.user_id != exclude_user_id)
        return int(s.scalar(q) or 0)
