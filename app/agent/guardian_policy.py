"""Decide whether to alert the guardian(s) for a verdict."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.agent.schema import Verdict
from app.db.models import Case, GuardianLink, User, VerdictRow
from app.db.session import get_session

# Don't spam the guardian: one alert per parent per category within this window.
COOLDOWN = timedelta(minutes=10)


def guardians_to_alert(parent_user_id: int | None, verdict: Verdict) -> list[User]:
    if parent_user_id is None or verdict.risk != "BAHAYA":
        return []
    with get_session() as s:
        guardians = s.scalars(
            select(User)
            .join(GuardianLink, GuardianLink.guardian_user_id == User.id)
            .where(GuardianLink.parent_user_id == parent_user_id, GuardianLink.active.is_(True))
        ).all()
        if not guardians:
            return []
        since = datetime.now(timezone.utc) - COOLDOWN
        recent = s.scalar(
            select(VerdictRow.id)
            .join(Case, Case.id == VerdictRow.case_id)
            .where(
                Case.user_id == parent_user_id,
                VerdictRow.guardian_alerted.is_(True),
                VerdictRow.category == verdict.category,
                VerdictRow.created_at >= since,
            )
            .limit(1)
        )
        return [] if recent else list(guardians)
