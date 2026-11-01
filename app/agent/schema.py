"""Verdict schema (PLAN.md section 7) plus the channel-free Input type."""
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

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


# JSON schema sent as response_format (kept identical to PLAN.md).
VERDICT_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["risk", "category", "confidence", "reason", "next_steps", "alert_guardian", "reply_text"],
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
    },
}

FALLBACK_VERDICT = Verdict(
    risk="WASPADA",
    category="lainnya",
    confidence=0.3,
    reason="Pesan ini belum bisa dicek dengan pasti.",
    next_steps=[
        "Jangan transfer uang atau kirim kode OTP dulu.",
        "Telepon balik orangnya lewat nomor lama yang Ibu/Bapak simpan.",
        "Tanyakan ke anak atau keluarga sebelum bertindak.",
    ],
    alert_guardian=False,
    reply_text="Maaf, sistem sedang kesulitan mengecek pesan ini. Sebaiknya hati-hati dulu.",
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
    qris_precheck: dict | None = None  # local QR scan of an image; the model reads it via decode_qris
