"""safe_word_check: does this parent's family have a safe word set up?"""
from sqlalchemy import select

from app.db.models import GuardianLink
from app.db.session import get_session

DEFAULT_QUESTION = (
    "Tanyakan hal yang hanya keluarga asli tahu, misalnya nama hewan peliharaan pertama, "
    "atau tutup dan telepon balik ke nomor lama yang tersimpan."
)


def safe_word_check(parent_user_id: int | None) -> dict:
    if parent_user_id is None:
        return {"has_safe_word": False, "guardian_linked": False, "suggested_question": DEFAULT_QUESTION}
    with get_session() as s:
        links = s.scalars(
            select(GuardianLink).where(GuardianLink.parent_user_id == parent_user_id, GuardianLink.active.is_(True))
        ).all()
        hint = next((l.safe_word_hint for l in links if l.safe_word_hint), None)
        linked = bool(links)
    if hint:
        return {
            "has_safe_word": True,
            "guardian_linked": linked,
            "suggested_question": f"Minta dia sebutkan kata aman keluarga (petunjuk: {hint}). "
            "Kalau tidak bisa, jangan transfer.",
        }
    return {"has_safe_word": False, "guardian_linked": linked, "suggested_question": DEFAULT_QUESTION}
