"""POST /analyze: channel-free entry point for curl and the test page."""
import base64
import binascii
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.agent.orchestrator import analyze
from app.agent.schema import Input, Verdict
from app.channels.formatting import format_reply
from app.db import repo

router = APIRouter()

MAX_MEDIA = 20 * 1024 * 1024


class AnalyzeRequest(BaseModel):
    type: Literal["text", "audio", "video", "image", "document"] = "text"
    text: str = Field(default="", max_length=8000)
    media_base64: str | None = None
    mime: str | None = None


class AnalyzeResponse(BaseModel):
    verdict: Verdict
    whatsapp_reply: str
    tools_used: list[str]
    extracted_text: str
    tool_outputs: dict
    provider: str
    model: str
    latency_ms: int
    case_id: int | None
    model_risk: str | None = None
    overrides: list[dict] = []
    image_mode: str | None = None


@router.post("/analyze", response_model=AnalyzeResponse)
def analyze_endpoint(req: AnalyzeRequest) -> AnalyzeResponse:
    media = None
    if req.media_base64:
        b64 = req.media_base64.split(",", 1)[1] if req.media_base64.startswith("data:") else req.media_base64
        try:
            media = base64.b64decode(b64, validate=False)
        except (binascii.Error, ValueError):
            raise HTTPException(400, "media_base64 is not valid base64")
        if len(media) > MAX_MEDIA:
            raise HTTPException(413, "media too large")
    if req.type in ("audio", "video", "image") and not media:
        raise HTTPException(400, f"type={req.type} needs media_base64")
    if req.type in ("text", "document") and not req.text.strip():
        raise HTTPException(400, "text is empty")

    inp = Input(type=req.type, text=req.text, media_bytes=media, mime=req.mime, channel="web")
    case_type = {"audio": "voice", "video": "voice", "document": "text"}.get(req.type, req.type)
    case_id = repo.create_case(None, "web", case_type, req.text)
    result = analyze(inp)
    if case_id:
        repo.save_verdict(case_id, result, input_text=result.extracted_text or req.text)

    outputs = dict(result.tool_outputs)
    if "decode_qris" in outputs:
        outputs["decode_qris"] = {k: v for k, v in outputs["decode_qris"].items() if k != "raw"}
    return AnalyzeResponse(
        verdict=result.verdict,
        whatsapp_reply=format_reply(result.verdict),
        tools_used=result.tools_used,
        extracted_text=result.extracted_text,
        tool_outputs=outputs,
        provider=result.provider,
        model=result.model,
        latency_ms=result.latency_ms,
        case_id=case_id,
        model_risk=result.model_risk,
        overrides=result.overrides,
        image_mode=result.image_mode,
    )
