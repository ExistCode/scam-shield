"""Tables from PLAN.md section 6. No media blobs are ever stored."""
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # One identity per channel; a row has wa_number (WhatsApp) or tg_chat_id (Telegram).
    wa_number: Mapped[str | None] = mapped_column(String(32), unique=True, index=True, nullable=True)  # plain for demo
    tg_chat_id: Mapped[str | None] = mapped_column(String(32), unique=True, index=True, nullable=True)
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    role: Mapped[str] = mapped_column(String(16), default="parent")  # parent / guardian
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class GuardianLink(Base):
    __tablename__ = "guardian_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    parent_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    guardian_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # Only a *hint* (e.g. "nama kucing pertama"); the safe word itself is never stored.
    safe_word_hint: Mapped[str | None] = mapped_column(String(200), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    parent: Mapped[User] = relationship(foreign_keys=[parent_user_id])
    guardian: Mapped[User] = relationship(foreign_keys=[guardian_user_id])


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    channel: Mapped[str] = mapped_column(String(8), default="wa")  # wa / tg / web
    input_type: Mapped[str] = mapped_column(String(8), default="text")  # text/voice/image/qr/link
    input_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "wa:<wamid>" or "tg:<chat_id>:<message_id>"; unique => dedupe of redelivered webhooks
    channel_message_id: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    verdict: Mapped["VerdictRow | None"] = relationship(back_populates="case", uselist=False)


class VerdictRow(Base):
    __tablename__ = "verdicts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), unique=True)
    risk: Mapped[str] = mapped_column(String(8))
    category: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text)
    next_steps: Mapped[list] = mapped_column(JSON)
    tools_used: Mapped[list] = mapped_column(JSON, default=list)
    model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    guardian_alerted: Mapped[bool] = mapped_column(Boolean, default=False)
    # {"model_risk": ..., "overrides": [...], "qris": {...}, "link_flags": [...]} : what Muse said vs. what we sent
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    case: Mapped[Case] = relationship(back_populates="verdict")
