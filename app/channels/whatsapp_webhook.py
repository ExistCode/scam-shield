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

from app.agent import followup, persona
from app.agent.orchestrator import analyze
from app.agent.schema import Input
from app.channels import notify, whatsapp_media, whatsapp_sender
from app.channels.formatting import format_reply, guardian_line, tools_footer, working_text
from app.config import settings
from app.db import repo

log = logging.getLogger("cekdulu.webhook")
router = APIRouter()

# Fast in-process dedupe (WhatsApp retries); the DB unique constraint is the real guard.
_seen: OrderedDict[str, None] = OrderedDict()
_SEEN_MAX = 2000

CONSENT = (
    "_Pesan yang diteruskan aku simpan sebagai teks buat pengecekan; foto dan suara nggak disimpan. "
    "Kalau lanjut pakai, berarti setuju ya._"
)

WELCOME = (
    "Halo! Aku *Cek Dulu* 👋\n\n"
    "Kalau dapat pesan, foto, atau voice note yang aneh, *terusin (forward)* aja ke sini. "
    "Nanti aku bilang itu penipuan atau bukan, dan harus ngapain.\n\n"
    + persona.ASK_PANGGILAN + "\n\n" + CONSENT
)

FIRST_TIME_NOTE = (
    "Aku *Cek Dulu* 👋 Kapan aja dapat pesan, foto, atau voice note yang aneh, terusin aja ke sini ya.\n\n"
    + persona.ASK_PANGGILAN + "\n\n" + CONSENT
)

HELP = (
    "*Cara pakai Cek Dulu*\n"
    "• Terusin pesan/foto/suara yang aneh ke sini.\n"
    "• *panggil <nama>* : ganti panggilanmu (misal: panggil Mama)\n"
    "• *daftar wali 08xx anak Hans* : daftarin anak/cucu/keluarga jadi wali\n"
    "• *hapus wali* : hapus semua wali\n"
    "• *kata aman <petunjuk>* : simpan *petunjuk* kata aman keluarga (jangan tulis kata aman-nya)\n"
    "• *gaya santai* / *gaya sopan* : atur gaya ngobrolku\n"
    "• *status* : lihat siapa aja yang ikut jagain"
)

_CMD_REGISTER = re.compile(r"^\s*daftar\s+wali\s+([+\d][\d\s\-]{6,20}\d)(?:\s+([a-z]+))?(?:\s+(\D{1,40}))?\s*$", re.I)
_CMD_UNLINK = re.compile(r"^\s*hapus\s+wali\s*$", re.I)
_CMD_SAFEWORD = re.compile(r"^\s*kata\s+aman\s+(.{2,200})$", re.I | re.S)
_CMD_HELP = re.compile(r"^\s*(bantuan|help|menu|halo|hai|hi|hello|mulai|start)\s*[!.?]*\s*$", re.I)
_CMD_STATUS = re.compile(r"^\s*status\s*$", re.I)
_CMD_PANGGIL = re.compile(r"^\s*panggil(?:\s+(?:aku|saya))?\s+(.{1,40})$", re.I)
_CMD_GAYA = re.compile(r"^\s*gaya\s+(santai|sopan)\s*$", re.I)


def verify_signature(body: bytes, header: str | None) -> bool:
    if not settings.wa_app_secret:
        if settings.production:
            log.error("WA_APP_SECRET not set; rejecting webhook in production")
            return False
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
    profile = repo.get_profile(user_id)
    p = persona.panggilan(profile)
    v = f", {p}" if p else ""
    if m := _CMD_REGISTER.match(text):
        number = repo.normalize_number(m.group(1))
        if not number:
            return "Nomor walinya nggak valid. Contoh: *daftar wali 081234567890 anak Hans*"
        if number == sender:
            return "Nomor wali harus beda dari nomormu sendiri ya."
        relation = (m.group(2) or "").lower()
        extra = m.group(3) or ""
        if relation and relation not in persona.RELATION_CODE:  # "daftar wali 08xx Hans"
            extra, relation = f"{m.group(2)} {extra}".strip(), ""
        relation = relation or "keluarga"
        guardian = repo.link_guardian(user_id, number)
        gname = (extra.strip().split() or [None])[0]
        gname = gname[:1].upper() + gname[1:] if gname else "+" + number[:4] + "****" + number[-3:]
        repo.add_profile_contact(user_id, gname, relation, guardian.id)
        try:
            whatsapp_sender.send_text(
                number,
                f"Halo! {_parent_label(name, sender)} daftarin kamu sebagai *wali* di Cek Dulu. "
                "Kamu bakal dikabarin kalau beliau dapat pesan penipuan yang bahaya.",
            )
        except Exception:
            log.info("guardian intro not delivered (likely outside 24h window / not a test recipient)")
        return (f"✅ {gname} ({relation}) sekarang ikut jagain{' ' + p if p else ''}. "
                f"Kalau ada pesan bahaya, {gname} langsung aku kabarin.\n\n"
                "Tips: bikin *kata aman* keluarga, terus simpan petunjuknya: *kata aman <petunjuk>*")
    if _CMD_UNLINK.match(text):
        n = repo.unlink_guardians(user_id)
        repo.clear_profile_contacts(user_id)
        return "Semua wali udah dihapus." if n else "Belum ada wali yang terdaftar."
    if m := _CMD_SAFEWORD.match(text):
        n = repo.set_safe_word_hint(user_id, m.group(1).strip())
        if not n:
            return "Daftarin wali dulu: *daftar wali 08xx anak Hans*, baru simpan petunjuk kata aman."
        return ("✅ Petunjuk kata aman udah disimpan. Kalau ada yang ngaku keluarga terus minta uang, "
                "suruh dia sebutin kata amannya.")
    if m := _CMD_GAYA.match(text):
        style = m.group(1).lower()
        repo.set_style(user_id, style)
        return ("Oke, aku ngobrolnya santai aja ya 😊" if style == "santai"
                else f"Baik{v}, mulai sekarang saya pakai bahasa yang lebih sopan.")
    if m := _CMD_PANGGIL.match(text):
        new = persona.parse_panggilan(m.group(1))
        if not new:
            return persona.ASK_PANGGILAN
        repo.set_panggilan(user_id, new)
        return persona.panggilan_saved(new, "*daftar wali 08xx anak Hans*")
    if _CMD_STATUS.match(text):
        fam = profile["contacts"]
        if not repo.active_guardians(user_id):
            return "Belum ada yang ikut jagain. Ketik: *daftar wali 08xxxxxxxxxx anak Hans*"
        head = f"Panggilanmu: *{p}*\n" if p else ""
        return head + "Yang ikut jagain:\n" + "\n".join(f"• {c['name']} ({c.get('relation') or 'keluarga'})" for c in fam)
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


def _handle_followup(text: str, user, sender: str, mid: str, profile: dict | None = None) -> bool:
    if followup.looks_like_new_content(text):
        return False
    last = repo.last_case_for(user.id, followup.WINDOW)
    if not last:
        return False
    ans = followup.answer(text, last, profile)
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
                    if reply is HELP:
                        reply = WELCOME
                        repo.set_awaiting(user.id, "panggilan")
                    else:
                        reply += "\n\n" + CONSENT
                whatsapp_sender.send_text(sender, reply, reply_to=mid)
                if first_time:
                    repo.set_consent(user.id)
                return
            forwarded = (msg.get("context") or {}).get("forwarded") or (msg.get("context") or {}).get("frequently_forwarded")
            profile = repo.get_profile(user.id)
            # Onboarding answer: "Mau aku panggil apa?" -> "Mama"
            if not forwarded and profile.get("awaiting") == "panggilan" \
                    and not repo.last_case_for(user.id, followup.WINDOW):
                name = persona.parse_panggilan(body)
                if name:
                    repo.set_panggilan(user.id, name)
                    whatsapp_sender.send_text(sender, persona.panggilan_saved(name, "*daftar wali 08xx anak Hans*"),
                                              reply_to=mid)
                    return
            if not forwarded and _handle_followup(body, user, sender, mid, profile):
                return

        inp, input_type = _build_input(msg, user.id)
        profile = repo.get_profile(user.id)
        if inp is not None:
            inp.profile = profile
        if inp is None:
            whatsapp_sender.send_text(
                sender, "Maaf, jenis pesan ini belum bisa aku cek. Coba terusin teks, foto, voice note, atau video ya.", reply_to=mid
            )
            return

        case_id = repo.create_case(user.id, "wa", input_type, inp.text, channel_message_id=f"wa:{mid}")
        if case_id is None:  # duplicate delivery
            return
        try:  # WhatsApp has no "typing…" for bots: say we're on it
            whatsapp_sender.send_text(sender, working_text(inp.type), reply_to=mid)
        except Exception:
            pass

        result = analyze(inp)
        repo.save_verdict(case_id, result, input_text=result.extracted_text or inp.text)
        inp.media_bytes = None  # drop media as soon as analysis is done

        # Alert first, so the reply can say truthfully whether the guardian was told.
        status, names = notify.alert_guardians(user.id, _parent_label(user.name, sender), result.verdict)
        if status == "sent":
            repo.mark_alerted(case_id)
        alerted = status == "sent"
        line = guardian_line(status, "*daftar wali 08xx anak Hans*", names, profile)
        whatsapp_sender.send_text(sender, format_reply(result.verdict, line, tools_footer(result), profile), reply_to=mid)
        if first_time:  # answer first, introduce ourselves after
            whatsapp_sender.send_text(sender, FIRST_TIME_NOTE)
            repo.set_consent(user.id)
            if not profile.get("panggilan"):
                repo.set_awaiting(user.id, "panggilan")

        log.info("case=%s risk=%s (muse=%s overrides=%s) tools=%s image=%s latency=%sms alerted=%s",
                 case_id, result.verdict.risk, result.model_risk, [o["rule"] for o in result.overrides],
                 result.tools_used, result.image_mode, result.latency_ms, alerted)
    except Exception:
        log.exception("processing message %s failed", mid)
        try:
            whatsapp_sender.send_text(
                sender, "Maaf, aku lagi ada gangguan pas ngecek. Sementara itu jangan transfer atau kirim OTP dulu ya.", reply_to=mid
            )
        except Exception:
            pass
