"""Web chat channel for the /wa demo page (two simulated phones: the parent and their guardian).

Same agent core and the same flow as the Telegram/WhatsApp adapters: onboarding ("Mau aku panggil
apa?"), commands, follow-ups, "Sedang dicek…", verdict, guardian alert. Bot messages are queued in
memory per demo session and the page polls /demo/inbox. Nothing here talks to WhatsApp.
"""
from __future__ import annotations

import base64
import binascii
import itertools
import logging
import re
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.agent import followup, persona
from app.agent.orchestrator import analyze_with_deadline
from app.agent.schema import Input
from app.api.analyze import MAX_MEDIA, _check_access
from app.channels import notify
from app.channels.formatting import format_reply, guardian_line, tools_footer, working_text
from app.db import repo

log = logging.getLogger("cekdulu.web")
router = APIRouter(prefix="/demo")

ASSETS = Path(__file__).resolve().parents[2] / "tests" / "manual" / "assets"
SESSION_RE = r"^[A-Za-z0-9_-]{8,40}$"
HINT = "*daftar wali anak Hans*"

CONSENT = ("_Pesan yang diteruskan aku simpan sebagai teks buat pengecekan; foto dan suara nggak disimpan. "
           "Kalau lanjut pakai, berarti setuju ya._")
WELCOME = ("Halo! Aku *Ronda* 👋\n\nKalau dapat pesan, foto, atau voice note yang aneh, *terusin (forward)* "
           "aja ke sini. Nanti aku bilang itu penipuan atau bukan, dan harus ngapain.\n\n"
           + persona.ASK_PANGGILAN + "\n\n" + CONSENT)
# Sent after the very first verdict: just ask what to call them. The intro and the privacy line come
# after they answer (panggilan_saved), so the verdict isn't buried under text.
FIRST_TIME_NOTE = persona.ASK_PANGGILAN
HELP = ("*Cara pakai Ronda*\n• Terusin pesan/foto/suara yang aneh ke sini.\n"
        "• *panggil <nama>* : ganti panggilanmu\n• *daftar wali anak Hans* : daftarin keluarga jadi wali\n"
        "• *kata aman <petunjuk>* : simpan petunjuk kata aman keluarga\n• *gaya santai* / *gaya sopan*\n"
        "• *status* : lihat siapa aja yang ikut jagain\n• *hapus wali* : hapus semua wali")

_HELLO = re.compile(r"(?i)^\s*/?(start|mulai|halo|hai|hi|hello|bantuan|help|menu)\s*[!.?]*\s*$")
_PANGGIL = re.compile(r"(?i)^\s*/?panggil(?:\s+(?:aku|saya))?\s+(.{1,40})$")
_GAYA = re.compile(r"(?i)^\s*/?gaya\s+(santai|sopan)\s*$")
_DAFTAR = re.compile(r"(?i)^\s*/?daftar[\s_]wali(?:\s+(.{1,60}))?$")
_HAPUS = re.compile(r"(?i)^\s*/?hapus[\s_]wali\s*$")
_STATUS = re.compile(r"(?i)^\s*/?status\s*$")
_KATA_AMAN = re.compile(r"(?i)^\s*/?kata[\s_]aman\s+(.{2,200})$")

# --------------------------------------------------------------------------- outbox (polled by the page)

_outbox: dict[str, list[dict]] = defaultdict(list)
_seq = itertools.count(1)
_lock = threading.Lock()


def push(web_id: str, text: str) -> None:
    """Queue a bot message for one simulated phone. web_id = "<session>:parent" or "<session>:g:<name>"."""
    session, who = web_id.split(":", 1)
    with _lock:
        box = _outbox[session]
        box.append({"id": next(_seq), "to": who, "text": text, "ts": time.time()})
        del box[:-300]


def _pid(session: str) -> str:
    return f"{session}:parent"


def _gid(session: str, name: str) -> str:
    return f"{session}:g:{re.sub(r'[^a-z0-9]', '', name.lower())[:20] or 'wali'}"


# --------------------------------------------------------------------------- API

class SendReq(BaseModel):
    session: str = Field(pattern=SESSION_RE)
    id: str = Field(max_length=40)
    type: Literal["text", "image", "audio", "video", "document"] = "text"
    text: str = Field(default="", max_length=4000)
    forwarded: bool = False
    media_base64: str | None = None
    mime: str | None = Field(default=None, max_length=80)
    filename: str | None = Field(default=None, max_length=120)


class SetupReq(BaseModel):
    session: str = Field(pattern=SESSION_RE)
    panggilan: str = Field(default="Mama", max_length=40)
    guardian: str = Field(default="Hans", max_length=30)
    relation: str = Field(default="anak", max_length=20)


@router.post("/send")
def send(req: SendReq, request: Request, background: BackgroundTasks,
         x_analyze_token: str | None = Header(default=None)):
    _check_access(request, x_analyze_token, per_min=30)
    if req.media_base64 and len(req.media_base64) > MAX_MEDIA * 4 // 3 + 64:
        raise HTTPException(413, "media too large")
    background.add_task(process, req)
    return {"ok": True}


@router.get("/inbox")
def inbox(session: str, after: int = 0):
    if not re.fullmatch(SESSION_RE, session):
        raise HTTPException(400, "bad session")
    with _lock:
        return {"messages": [m for m in _outbox.get(session, []) if m["id"] > after]}


@router.post("/setup")
def setup(req: SetupReq, request: Request, x_analyze_token: str | None = Header(default=None)):
    """Prepare a clean demo: the parent already has a panggilan and a linked guardian, no chat history."""
    _check_access(request, x_analyze_token, per_min=30)
    parent, _ = repo.upsert_web_user(_pid(req.session))
    name = persona.parse_panggilan(req.panggilan) or "Mama"
    repo.set_panggilan(parent.id, name)
    repo.set_consent(parent.id)
    _link(req.session, parent.id, req.guardian.strip() or "Hans", req.relation.lower(), announce=False)
    return {"ok": True, "panggilan": name, "guardian": req.guardian}


@router.get("/assets/{name}")
def asset(name: str):
    path = (ASSETS / name).resolve()
    if path.parent != ASSETS or not path.is_file() or path.suffix not in {".png", ".jpg", ".ogg", ".mp4"}:
        raise HTTPException(404)
    return FileResponse(path)


# --------------------------------------------------------------------------- processing

def _link(session: str, parent_id: int, name: str, relation: str, announce: bool = True) -> str:
    relation = relation if relation in persona.RELATION_CODE else "keluarga"
    name = name[:1].upper() + name[1:]
    guardian, _ = repo.upsert_web_user(_gid(session, name), name, role="guardian")
    repo.link_guardian_user(parent_id, guardian.id)
    repo.add_profile_contact(parent_id, name, relation, guardian.id)
    p = persona.panggilan(repo.get_profile(parent_id))
    if announce:
        push(guardian.web_id, f"Halo {name}! 👋 {p or 'Keluargamu'} daftarin kamu sebagai *wali* di Ronda. "
                              f"Kalau {p or 'dia'} dapet pesan penipuan yang bahaya, kamu langsung aku kabarin di sini.")
    return (f"✅ {name} ({relation}) sekarang ikut jagain{' ' + p if p else ''}. "
            f"Kalau ada pesan bahaya, {name} langsung aku kabarin.")


def _command(text: str, user, session: str) -> str | None:
    profile = repo.get_profile(user.id)
    p = persona.panggilan(profile)
    v = f", {p}" if p else ""
    if _HELLO.match(text):
        if p:
            return f"Halo{v}! 👋 Terusin aja pesan, foto, atau voice note yang aneh ke sini, nanti aku cek."
        repo.set_awaiting(user.id, "panggilan")
        return WELCOME
    if m := _PANGGIL.match(text):
        name = persona.parse_panggilan(m.group(1))
        if not name:
            return persona.ASK_PANGGILAN
        repo.set_panggilan(user.id, name)
        return persona.panggilan_saved(name, HINT)
    if m := _GAYA.match(text):
        repo.set_style(user.id, m.group(1).lower())
        return "Oke, aku ngobrolnya santai aja ya 😊" if m.group(1).lower() == "santai" else \
            f"Baik{v}, mulai sekarang saya pakai bahasa yang lebih sopan."
    if m := _DAFTAR.match(text):
        words = (m.group(1) or "").split()
        relation = words[0].lower() if words and words[0].lower() in persona.RELATION_CODE else ""
        names = words[1:] if relation else words
        if not names:
            return f"Tulis nama walinya ya, contoh: {HINT}"
        return _link(session, user.id, names[0], relation or "keluarga")
    if _HAPUS.match(text):
        n = repo.unlink_guardians(user.id)
        repo.clear_profile_contacts(user.id)
        return "Semua wali udah dihapus." if n else "Belum ada wali yang terdaftar."
    if m := _KATA_AMAN.match(text):
        if not repo.set_safe_word_hint(user.id, m.group(1).strip()):
            return f"Daftarin wali dulu: {HINT}, baru simpan petunjuk kata aman."
        return "✅ Petunjuk kata aman udah disimpan."
    if _STATUS.match(text):
        fam = profile["contacts"]
        if not fam:
            return f"Belum ada yang ikut jagain. Ketik: {HINT}"
        head = f"Panggilanmu: *{p}*\n" if p else ""
        return head + "Yang ikut jagain:\n" + "\n".join(f"• {c['name']} ({c.get('relation') or 'keluarga'})" for c in fam)
    if text.strip().lower() in ("/bantuan", "bantuan"):
        return HELP
    return None


def _build_input(req: SendReq, user_id: int, profile: dict) -> tuple[Input, str]:
    media = None
    if req.media_base64:
        b64 = req.media_base64.split(",", 1)[1] if req.media_base64.startswith("data:") else req.media_base64
        try:
            media = base64.b64decode(b64)
        except (binascii.Error, ValueError):
            media = None
    if req.type == "document":
        text = f"{req.text}\n[File terlampir: {req.filename or 'tanpa nama'} ({req.mime or '?'})]".strip()
        return Input(type="document", text=text, user_id=user_id, channel="web", profile=profile), "text"
    if req.type in ("image", "audio", "video") and media:
        kind = {"image": "image", "audio": "voice", "video": "voice"}[req.type]
        return Input(type=req.type, text=req.text, media_bytes=media, mime=req.mime, user_id=user_id,
                     channel="web", profile=profile), kind
    return Input(type="text", text=req.text, user_id=user_id, channel="web", profile=profile), "text"


def process(req: SendReq) -> None:
    pid = _pid(req.session)
    try:
        user, is_new = repo.upsert_web_user(pid)
        first_time = is_new or not user.consent_at
        profile = repo.get_profile(user.id)
        text = (req.text or "").strip()

        if req.type == "text" and not req.forwarded and text:
            reply = _command(text, user, req.session)
            if reply:
                push(pid, reply if (not first_time or reply == WELCOME) else reply + "\n\n" + CONSENT)
                repo.set_consent(user.id)
                return
            if profile.get("awaiting") == "panggilan":
                name = persona.parse_panggilan(text, strict=bool(repo.last_case_for(user.id, followup.WINDOW)))
                if name:
                    repo.set_panggilan(user.id, name)
                    push(pid, persona.panggilan_saved(name, HINT, CONSENT))
                    return
            last = None if followup.looks_like_new_content(text) else repo.last_case_for(user.id, followup.WINDOW)
            if last:
                ans = followup.answer(text, last, profile)
                if ans and ans.intent == "followup":
                    push(pid, ans.reply)
                    if ans.already_paid:
                        label = persona.panggilan(profile) or "Keluargamu"
                        notify.alert_urgent(user.id, label, followup.urgent_verdict(last))
                    return

        inp, input_type = _build_input(req, user.id, profile)
        case_id = repo.create_case(user.id, "web", input_type, inp.text, channel_message_id=f"web:{req.session}:{req.id}")
        if case_id is None:
            return
        push(pid, working_text(inp.type))
        result = analyze_with_deadline(inp)
        repo.save_verdict(case_id, result, input_text=result.extracted_text or inp.text)
        inp.media_bytes = None

        label = persona.panggilan(profile) or "Keluargamu"
        status, names = notify.alert_guardians(user.id, label, result.verdict)
        if status == "sent":
            repo.mark_alerted(case_id)
        line = guardian_line(status, HINT, names, profile)
        push(pid, format_reply(result.verdict, line, tools_footer(result), profile))
        if first_time:
            push(pid, FIRST_TIME_NOTE)
            repo.set_consent(user.id)
            if not profile.get("panggilan"):
                repo.set_awaiting(user.id, "panggilan")
        log.info("web case=%s risk=%s tools=%s latency=%sms guardian=%s", case_id, result.verdict.risk,
                 result.tools_used, result.latency_ms, status)
    except Exception:
        log.exception("web demo message failed")
        push(pid, "Maaf, aku lagi ada gangguan pas ngecek. Sementara itu jangan transfer atau kirim OTP dulu ya.")
