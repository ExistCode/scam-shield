"""Verdict schema plus the channel-free Input type."""
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from app.agent.contacts import SCAM_TYPES, ScamType

Risk = Literal["AMAN", "WASPADA", "BAHAYA"]
Category = Literal[
    "kurir_apk", "minta_pulsa", "cs_bank_palsu", "aparat_palsu", "investasi", "belanja_online",
    "lowongan_kerja", "hadiah_palsu", "pinjol", "qris_palsu", "hoaks", "lainnya"
]


class Verdict(BaseModel):
    risk: Risk
    category: Category
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1)
    next_steps: list[str] = Field(min_length=1, max_length=3)
    alert_guardian: bool
    reply_text: str = Field(min_length=1)
    # The model only names the type; the code picks contacts from app/agent/contacts.py.
    scam_type: ScamType = "other"
    money_sent: bool = False  # content shows the parent already transferred / sent OTP / installed
    # Numbers the scammer uses, as the model read them (text, screenshot, voice). Code verifies/normalizes.
    scam_phone_numbers: list[str] = Field(default_factory=list)
    scam_account_numbers: list[str] = Field(default_factory=list)  # bank / e-wallet accounts to transfer to
    lookups: list[str] = Field(default_factory=list)  # code-written lines, e.g. "sudah dilaporkan 12x ..."
    contacts: list[dict] = Field(default_factory=list)  # filled by code, never by the model


# JSON schema sent as response_format.
VERDICT_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["risk", "category", "confidence", "reason", "next_steps", "alert_guardian", "reply_text",
                 "scam_type", "money_sent", "scam_phone_numbers", "scam_account_numbers"],
    "properties": {
        "risk": {"type": "string", "enum": ["AMAN", "WASPADA", "BAHAYA"]},
        "category": {
            "type": "string",
            "enum": [
                "kurir_apk", "minta_pulsa", "cs_bank_palsu", "aparat_palsu", "investasi", "belanja_online",
                "lowongan_kerja", "hadiah_palsu", "pinjol", "qris_palsu", "hoaks", "lainnya"
            ],
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
        "next_steps": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 3},
        "alert_guardian": {"type": "boolean"},
        "reply_text": {"type": "string"},
        "scam_type": {"type": "string", "enum": SCAM_TYPES},
        "money_sent": {"type": "boolean"},
        "scam_phone_numbers": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
        "scam_account_numbers": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
    },
}

FALLBACK_VERDICT = Verdict(
    risk="WASPADA",
    category="lainnya",
    confidence=0.3,
    reason="Pesan ini belum bisa aku cek dengan pasti.",
    next_steps=[
        "Jangan transfer atau kirim kode OTP dulu.",
        "Telepon balik orangnya lewat nomor lama yang tersimpan.",
        "Tanya anak atau keluarga dulu sebelum bertindak.",
    ],
    alert_guardian=False,
    reply_text="Maaf, aku lagi susah ngecek pesan ini. Hati-hati dulu ya.",
)


InputType = Literal["text", "audio", "video", "image", "document"]


@dataclass
class Input:
    """Channel-free input handed to the agent core."""

    type: InputType
    text: str = ""
    media_bytes: bytes | None = None
    mime: str | None = None
    user_id: int | None = None  # DB user id (for safe-word lookup)
    channel: str = "web"
    profile: dict | None = None  # persona: panggilan, bahasa_style, family contacts (repo.get_profile)


@dataclass
class AnalysisResult:
    verdict: Verdict
    tools_used: list[str] = field(default_factory=list)
    extracted_text: str = ""  # transcript / caption / qr summary, persisted as case input_text
    model: str = ""
    provider: str = ""
    latency_ms: int = 0
    tool_outputs: dict = field(default_factory=dict)
    model_risk: str | None = None  # what the model said before our safety floors
    overrides: list[dict] = field(default_factory=list)  # [{"rule", "field", "from", "to"}]
    image_mode: str | None = None  # "single_call" | "two_step"
    escalation: dict | None = None  # Muse Wonder second opinion: {"spark": {...}, "wonder": {...} | None}
    conversation: list | None = None  # Spark's messages incl. tool results (in memory only, for escalation)
    qris_precheck: dict | None = None  # local QR scan of an image; the model reads it via decode_qris
