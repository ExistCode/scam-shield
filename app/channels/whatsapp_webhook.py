"""WhatsApp webhook adapter: verify, dedupe, return 200 fast, process in the background."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from collections import OrderedDict

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import PlainTextResponse

from app.agent import followup
from app.agent.orchestrator import analyze
from app.agent.schema import Input
from app.channels import notify, whatsapp_media, whatsapp_sender
from app.channels.formatting import format_reply, guardian_line
from app.config import settings
from app.db import repo

log = logging.getLogger("cekdulu.webhook")
router = APIRouter()

# Fast in-process dedupe (WhatsApp retries); the DB unique constraint is the real guard.
_seen: OrderedDict[str, None] = OrderedDict()
_SEEN_MAX = 2000

CONSENT = (
    "_Pesan yang diteruskan disimpan sebagai teks untuk pengecekan; foto dan suara tidak disimpan. "
    "Dengan melanjutkan, Ibu/Bapak setuju dengan ini._"
)

WELCOME = (
    "Halo, saya *Cek Dulu* 👋\n\n"
    "Kalau dapat pesan, foto, atau pesan suara yang mencurigakan, cukup *teruskan (forward)* ke sini. "
    "Saya akan bilang apakah itu penipuan dan apa yang sebaiknya dilakukan.\n\n"
    "Daftarkan anak/keluarga sebagai *wali* supaya mereka dikabari kalau ada bahaya:\n"
    "ketik: *daftar wali 08xxxxxxxxxx*\n\n"
    + CONSENT
)

FIRST_TIME_NOTE = (
    "Saya *Cek Dulu* 👋 Kapan saja dapat pesan, foto, atau pesan suara yang mencurigakan, "
    "teruskan saja ke sini.\n"
    "Supaya anak/keluarga ikut dikabari kalau ada bahaya, ketik: *daftar wali 08xxxxxxxxxx*\n\n"
    + CONSENT
)

HELP = (
    "*Cara pakai Cek Dulu*\n"
    "• Teruskan pesan/foto/suara yang mencurigakan ke sini.\n"
    "• *daftar wali 08xx* : daftarkan anak/keluarga sebagai wali\n"
    "• *hapus wali* : hapus semua wali\n"
    "• *kata aman <petunjuk>* : simpan *petunjuk* kata aman keluarga (jangan tulis kata aman-nya)\n"
    "• *status* : lihat wali yang terdaftar"
)

_CMD_REGISTER = re.compile(r"^\s*daftar\s+wali\s+([+\d][\d\s\-]{7,20})\s*$", re.I)
_CMD_UNLINK = re.compile(r"^\s*hapus\s+wali\s*$", re.I)
_CMD_SAFEWORD = re.compile(r"^\s*kata\s+aman\s+(.{2,200})$", re.I | re.S)
_CMD_HELP = re.compile(r"^\s*(bantuan|help|menu|halo|hai|hi|hello|mulai|start)\s*[!.?]*\s*$", re.I)
_CMD_STATUS = re.compile(r"^\s*status\s*$", re.I)


def verify_signature(body: bytes, header: str | None) -> bool:
    if not settings.wa_app_secret:
        log.warning("WA_APP_SECRET not set; skipping signature check (dev only)")
        return True
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(settings.wa_app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.split("=", 1)[1])


@router.get("/webhook")
def verify(request: Request):
    q = request.query_params
    if q.get("hub.mode") == "subscribe" and settings.wa_verify_token and q.get("hub.verify_token") == settings.wa_verify_token:
        return PlainTextResponse(q.get("hub.challenge", ""))
    raise HTTPException(status_code=403, detail="verification failed")


@router.post("/webhook")
async def receive(request: Request, background: BackgroundTasks):
    body = await request.body()
    if not verify_signature(body, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=401, detail="bad signature")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return {"ok": True}

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            names = {c.get("wa_id"): c.get("profile", {}).get("name") for c in value.get("contacts", [])}
            for msg in value.get("messages", []):
                mid = msg.get("id")
                if not mid or mid in _seen:
                    continue
                _seen[mid] = None
                if len(_seen) > _SEEN_MAX:
                    _seen.popitem(last=False)
                background.add_task(process_message, msg, names.get(msg.get("from")))
    return {"ok": True}


# --------------------------------------------------------------------------- processing

def _parent_label(name: str | None, number: str) -> str:
    masked = number[:4] + "****" + number[-3:] if len(number) > 7 else number
    return f"{name} ({masked})" if name else masked


def _handle_command(text: str, user_id: int, sender: str, name: str | None) -> str | None:
    """Returns a reply if the text is a command, else None."""
    if m := _CMD_REGISTER.match(text):
        number = repo.normalize_number(m.group(1))
        if not number:
            return "Nomor wali tidak valid. Contoh: *daftar wali 081234567890*"
        if number == sender:
            return "Nomor wali harus berbeda dari nomor Ibu/Bapak sendiri."
        repo.link_guardian(user_id, number)
        try:
            whatsapp_sender.send_text(
                number,
                f"Halo! {_parent_label(name, sender)} mendaftarkan Anda sebagai *wali* di Cek Dulu. "
                "Anda akan dikabari kalau beliau menerima pesan penipuan yang berbahaya.",
            )
        except Exception:
            log.info("guardian intro not delivered (likely outside 24h window / not a test recipient)")
        return (f"✅ Wali terdaftar: +{number}\nKalau ada pesan BAHAYA, wali akan langsung dikabari.\n\n"
                "Tips: buat *kata aman* keluarga, lalu simpan petunjuknya dengan: *kata aman <petunjuk>*")
    if _CMD_UNLINK.match(text):
        n = repo.unlink_guardians(user_id)
        return "Semua wali sudah dihapus." if n else "Belum ada wali yang terdaftar."
    if m := _CMD_SAFEWORD.match(text):
        n = repo.set_safe_word_hint(user_id, m.group(1).strip())
        if not n:
            return "Daftarkan wali dulu: *daftar wali 08xx*, lalu simpan petunjuk kata aman."
        return ("✅ Petunjuk kata aman disimpan. Kalau ada yang mengaku keluarga dan minta uang, "
                "minta dia sebutkan kata aman itu.")
    if _CMD_STATUS.match(text):
        gs = repo.active_guardians(user_id)
        if not gs:
            return "Belum ada wali. Ketik: *daftar wali 08xxxxxxxxxx*"
        return "Wali terdaftar:\n" + "\n".join(f"• {notify.describe(g)}" for g in gs)
    if _CMD_HELP.match(text):
        return HELP
    return None


def _build_input(msg: dict, user_id: int) -> tuple[Input | None, str]:
    """Returns (Input, case input_type) or (None, reason) for unsupported types."""
    mtype = msg.get("type")
    if mtype == "text":
        return Input(type="text", text=msg["text"].get("body", ""), user_id=user_id, channel="wa"), "text"
    if mtype == "audio":
        data, mime = whatsapp_media.fetch_media(msg["audio"]["id"])
        return Input(type="audio", media_bytes=data, mime=mime, user_id=user_id, channel="wa"), "voice"
    if mtype == "video":
        data, mime = whatsapp_media.fetch_media(msg["video"]["id"])
        return Input(type="video", text=msg["video"].get("caption", ""), media_bytes=data, mime=mime,
                     user_id=user_id, channel="wa"), "voice"
    if mtype == "image":
        data, mime = whatsapp_media.fetch_media(msg["image"]["id"])
        caption = msg["image"].get("caption", "")
        return Input(type="image", text=caption, media_bytes=data, mime=mime, user_id=user_id, channel="wa"), "image"
    if mtype == "document":
        doc = msg["document"]
        # We never open/download documents; the filename and caption are enough to spot APK lures.
        text = f"{doc.get('caption', '')}\n[File terlampir: {doc.get('filename', 'tanpa nama')} ({doc.get('mime_type', '?')})]"
        return Input(type="document", text=text.strip(), user_id=user_id, channel="wa"), "text"
    return None, mtype or "unknown"


def _handle_followup(text: str, user, sender: str, mid: str) -> bool:
    if followup.looks_like_new_content(text):
        return False
    last = repo.last_case_for(user.id, followup.WINDOW)
    if not last:
        return False
    ans = followup.answer(text, last)
    if not ans or ans.intent != "followup":
        return False
    whatsapp_sender.send_text(sender, ans.reply, reply_to=mid)
    if ans.already_paid:
        for g in repo.active_guardians(user.id):
            notify.alert_guardian(g, _parent_label(user.name, sender), followup.urgent_verdict(last))
    log.info("wa follow-up for case=%s already_paid=%s", last["case_id"], ans.already_paid)
    return True


def process_message(msg: dict, profile_name: str | None) -> None:
    sender = msg.get("from", "")
    mid = msg.get("id")
    try:
        user, is_new = repo.upsert_user(sender, profile_name)
        whatsapp_sender.mark_read(mid)
        first_time = is_new or not user.consent_at

        if msg.get("type") == "text":
            body = msg["text"].get("body", "")
            reply = _handle_command(body, user.id, sender, user.name)
            if reply:
                if first_time:
                    reply = WELCOME if reply is HELP else reply + "\n\n" + CONSENT
                whatsapp_sender.send_text(sender, reply, reply_to=mid)
                if first_time:
                    repo.set_consent(user.id)
                return
            forwarded = (msg.get("context") or {}).get("forwarded") or (msg.get("context") or {}).get("frequently_forwarded")
            if not forwarded and _handle_followup(body, user, sender, mid):
                return

        inp, input_type = _build_input(msg, user.id)
        if inp is None:
            whatsapp_sender.send_text(
                sender, "Maaf, jenis pesan ini belum bisa dicek. Coba teruskan teks, foto, pesan suara, atau video.", reply_to=mid
            )
            return

        case_id = repo.create_case(user.id, "wa", input_type, inp.text, channel_message_id=f"wa:{mid}")
        if case_id is None:  # duplicate delivery
            return

        result = analyze(inp)
        repo.save_verdict(case_id, result, input_text=result.extracted_text or inp.text)
        inp.media_bytes = None  # drop media as soon as analysis is done

        # Alert first, so the reply can say truthfully whether the guardian was told.
        status = notify.alert_guardians(user.id, _parent_label(user.name, sender), result.verdict)
        if status == "sent":
            repo.mark_alerted(case_id)
        alerted = status == "sent"
        hint = "" if first_time else "*daftar wali 08xxxxxxxxxx*"
        whatsapp_sender.send_text(sender, format_reply(result.verdict, guardian_line(status, hint)), reply_to=mid)
        if first_time:  # answer first, introduce ourselves after
            whatsapp_sender.send_text(sender, FIRST_TIME_NOTE)
            repo.set_consent(user.id)

        log.info("case=%s risk=%s (muse=%s overrides=%s) tools=%s image=%s latency=%sms alerted=%s",
                 case_id, result.verdict.risk, result.model_risk, [o["rule"] for o in result.overrides],
                 result.tools_used, result.image_mode, result.latency_ms, alerted)
    except Exception:
        log.exception("processing message %s failed", mid)
        try:
            whatsapp_sender.send_text(
                sender, "Maaf, ada gangguan saat mengecek. Sementara itu, jangan transfer atau kirim OTP dulu ya.", reply_to=mid
            )
        except Exception:
            pass
