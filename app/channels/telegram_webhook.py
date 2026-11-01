"""Telegram adapter: same agent core as WhatsApp, Telegram-native guardian linking.

A bot can't message someone by phone number, so guardians link themselves: the parent gets a
deep link (t.me/<bot>?start=wali_<id>_<sig>), sends it to their child, the child taps it.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
from collections import OrderedDict

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from app.agent import followup
from app.agent.orchestrator import analyze
from app.agent.schema import Input
from app.channels import notify, telegram_bot
from app.channels.formatting import format_reply, guardian_line
from app.config import settings
from app.db import repo

log = logging.getLogger("cekdulu.tg")
router = APIRouter()

_seen: OrderedDict[int, None] = OrderedDict()
_SEEN_MAX = 2000

CONSENT = (
    "_Pesan yang diteruskan disimpan sebagai teks untuk pengecekan; foto dan suara tidak disimpan. "
    "Dengan melanjutkan, Ibu/Bapak setuju dengan ini._"
)

WELCOME = (
    "Halo, saya *Cek Dulu* 👋\n\n"
    "Kalau dapat pesan, foto, atau pesan suara yang mencurigakan, cukup *teruskan (forward)* ke sini. "
    "Saya akan bilang apakah itu penipuan dan apa yang sebaiknya dilakukan.\n\n"
    "Supaya anak/keluarga dikabari kalau ada bahaya, ketik /daftar_wali\n\n"
    + CONSENT
)

# Sent after the first verdict, when someone's very first message was something to check.
FIRST_TIME_NOTE = (
    "Saya *Cek Dulu* 👋 Kapan saja dapat pesan, foto, atau pesan suara yang mencurigakan, "
    "teruskan saja ke sini.\n"
    "Supaya anak/keluarga ikut dikabari kalau ada bahaya, ketik /daftar_wali\n\n"
    + CONSENT
)

HELP = (
    "*Cara pakai Cek Dulu*\n"
    "• Teruskan pesan/foto/suara yang mencurigakan ke sini.\n"
    "• /daftar_wali : dapatkan link untuk anak/keluarga jadi wali\n"
    "• /kata_aman petunjuk : simpan *petunjuk* kata aman keluarga (jangan tulis kata aman-nya)\n"
    "• /status : lihat wali yang terdaftar\n"
    "• /hapus_wali : hapus semua wali"
)

# Accept both slash commands and the plain Indonesian phrases used on WhatsApp.
_CMD = re.compile(r"^\s*/([a-z_]+)(?:@\w+)?(?:\s+(.*))?$", re.I | re.S)
_PLAIN = {
    re.compile(r"^\s*daftar\s+wali\b.*$", re.I | re.S): "daftar_wali",
    re.compile(r"^\s*hapus\s+wali\s*$", re.I): "hapus_wali",
    re.compile(r"^\s*status\s*$", re.I): "status",
    re.compile(r"^\s*(bantuan|help|menu|halo|hai|hi|hello|mulai)\s*[!.?]*\s*$", re.I): "bantuan",
}
_PLAIN_SAFEWORD = re.compile(r"^\s*kata\s+aman\s+(.{2,200})$", re.I | re.S)


# --------------------------------------------------------------------------- invite tokens

def _invite_sig(parent_id: int) -> str:
    key = hashlib.sha256(b"cekdulu-invite:" + settings.telegram_bot_token.encode()).digest()
    return hmac.new(key, str(parent_id).encode(), hashlib.sha256).hexdigest()[:16]


def make_invite(parent_id: int) -> str:
    return f"wali_{parent_id}_{_invite_sig(parent_id)}"  # <= 64 chars, [A-Za-z0-9_]


def parse_invite(payload: str) -> int | None:
    m = re.fullmatch(r"wali_(\d{1,12})_([0-9a-f]{16})", payload or "")
    if not m:
        return None
    pid = int(m.group(1))
    return pid if hmac.compare_digest(_invite_sig(pid), m.group(2)) else None


# --------------------------------------------------------------------------- webhook

@router.post("/telegram/webhook")
async def receive(request: Request, background: BackgroundTasks):
    if settings.telegram_webhook_secret:
        got = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not hmac.compare_digest(got, settings.telegram_webhook_secret):
            raise HTTPException(status_code=401, detail="bad secret")
    elif settings.production:
        raise HTTPException(status_code=503, detail="TELEGRAM_WEBHOOK_SECRET not set")
    else:
        log.warning("TELEGRAM_WEBHOOK_SECRET not set; accepting unauthenticated updates (dev only)")
    update = await request.json()
    uid = update.get("update_id")
    if uid is None or uid in _seen:
        return {"ok": True}
    _seen[uid] = None
    if len(_seen) > _SEEN_MAX:
        _seen.popitem(last=False)
    background.add_task(process_update, update)
    return {"ok": True}


# --------------------------------------------------------------------------- processing

def _label(user_obj: dict) -> str:
    name = " ".join(filter(None, [user_obj.get("first_name"), user_obj.get("last_name")])) or "Pengguna"
    return f"{name} (@{user_obj['username']})" if user_obj.get("username") else name


def _parse_command(text: str) -> tuple[str, str] | None:
    if m := _CMD.match(text):
        return m.group(1).lower(), (m.group(2) or "").strip()
    if m := _PLAIN_SAFEWORD.match(text):
        return "kata_aman", m.group(1).strip()
    for rx, cmd in _PLAIN.items():
        if rx.match(text):
            return cmd, ""
    return None


def _handle_command(cmd: str, arg: str, user, chat_id: int, label: str) -> str | None:
    if cmd in ("start",) and arg:
        parent_id = parse_invite(arg)
        if parent_id is None:
            return "Link wali tidak valid. Minta link baru dari keluarga Anda."
        if parent_id == user.id:
            return ("Ini link wali milik Ibu/Bapak sendiri 🙂\n"
                    "Kirim link itu ke anak/keluarga, lalu minta mereka membukanya.")
        parent = repo.get_user(parent_id)
        if parent is None:
            return "Link wali tidak valid. Minta link baru dari keluarga Anda."
        repo.link_guardian_user(parent_id, user.id)
        parent_name = parent.name or "keluarga Anda"
        if parent.tg_chat_id:
            try:
                telegram_bot.send_text(parent.tg_chat_id, f"✅ *{label}* sekarang jadi wali Ibu/Bapak. "
                                       "Kalau ada pesan BAHAYA, dia akan langsung dikabari.")
            except Exception:
                log.info("could not notify parent of new guardian")
        return (f"✅ Anda sekarang *wali* untuk {parent_name} di Cek Dulu.\n"
                "Anda akan dikabari di sini kalau beliau menerima pesan penipuan yang berbahaya.")
    if cmd == "start":
        return WELCOME
    if cmd == "daftar_wali":
        username = telegram_bot.bot_username()
        link = f"https://t.me/{username}?start={make_invite(user.id)}"
        return ("Kirim link ini ke anak/keluarga yang mau jadi wali, lalu minta mereka membukanya "
                f"dan tekan *Start*:\n\n{link}\n\n"
                "_Di Telegram, wali harus membuka link ini sendiri; nomor HP saja tidak cukup._")
    if cmd == "hapus_wali":
        return "Semua wali sudah dihapus." if repo.unlink_guardians(user.id) else "Belum ada wali yang terdaftar."
    if cmd == "kata_aman":
        if not arg:
            return "Tulis petunjuknya, contoh: /kata_aman nama kucing pertama kita"
        if not repo.set_safe_word_hint(user.id, arg):
            return "Daftarkan wali dulu dengan /daftar_wali, lalu simpan petunjuk kata aman."
        return ("✅ Petunjuk kata aman disimpan. Kalau ada yang mengaku keluarga dan minta uang, "
                "minta dia sebutkan kata aman itu.")
    if cmd == "status":
        gs = repo.active_guardians(user.id)
        if not gs:
            return "Belum ada wali. Ketik /daftar_wali"
        return "Wali terdaftar:\n" + "\n".join(f"• {notify.describe(g)}" for g in gs)
    if cmd in ("bantuan", "help"):
        return HELP
    return None  # unknown slash command -> analyze as text


def _forward_note(msg: dict) -> str:
    origin = msg.get("forward_origin") or {}
    who = (origin.get("sender_user") or {}).get("first_name") or origin.get("sender_user_name") \
        or (origin.get("chat") or {}).get("title")
    return f"[Diteruskan dari: {who}]" if who else ""


def _build_input(msg: dict, user_id: int) -> tuple[Input | None, str]:
    caption = msg.get("caption") or ""
    note = _forward_note(msg)

    def with_note(t: str) -> str:
        return "\n".join(filter(None, [note, t]))

    if "text" in msg:
        return Input(type="text", text=with_note(msg["text"]), user_id=user_id, channel="tg"), "text"
    if "voice" in msg or "audio" in msg:
        a = msg.get("voice") or msg["audio"]
        data = telegram_bot.download_file(a["file_id"])
        return Input(type="audio", text=with_note(caption), media_bytes=data, mime=a.get("mime_type"),
                     user_id=user_id, channel="tg"), "voice"
    if "video" in msg or "video_note" in msg:
        v = msg.get("video") or msg["video_note"]
        data = telegram_bot.download_file(v["file_id"])
        return Input(type="video", text=with_note(caption), media_bytes=data, mime=v.get("mime_type", "video/mp4"),
                     user_id=user_id, channel="tg"), "voice"
    if "photo" in msg:
        data = telegram_bot.download_file(msg["photo"][-1]["file_id"])  # largest size
        return Input(type="image", text=with_note(caption), media_bytes=data, mime="image/jpeg",
                     user_id=user_id, channel="tg"), "image"
    if "document" in msg:
        doc = msg["document"]
        mime = doc.get("mime_type") or ""
        if mime.startswith("image/"):  # photo sent "as file" keeps full resolution: good for QRIS
            data = telegram_bot.download_file(doc["file_id"])
            return Input(type="image", text=with_note(caption), media_bytes=data, mime=mime,
                         user_id=user_id, channel="tg"), "image"
        if mime.startswith(("audio/", "video/")):  # media dragged in as a file (e.g. Telegram Desktop)
            data = telegram_bot.download_file(doc["file_id"])
            return Input(type="video" if mime.startswith("video/") else "audio", text=with_note(caption), media_bytes=data, mime=mime,
                         user_id=user_id, channel="tg"), "voice"
        # Never open other files; filename + caption are enough to spot APK lures.
        text = f"{caption}\n[File terlampir: {doc.get('file_name', 'tanpa nama')} ({mime or '?'})]"
        return Input(type="document", text=with_note(text.strip()), user_id=user_id, channel="tg"), "text"
    return None, "unsupported"


def _handle_followup(text: str, user, chat_id: int, mid: int, label: str) -> bool:
    """Returns True if the message was answered as a follow-up."""
    if followup.looks_like_new_content(text):
        return False
    last = repo.last_case_for(user.id, followup.WINDOW)
    if not last:
        return False
    with telegram_bot.typing(chat_id):
        ans = followup.answer(text, last)
    if not ans or ans.intent != "followup":
        return False
    telegram_bot.send_text(chat_id, ans.reply, reply_to=mid)
    if ans.already_paid:
        for g in repo.active_guardians(user.id):  # no cooldown: this is the emergency
            notify.alert_guardian(g, label, followup.urgent_verdict(last))
    log.info("tg follow-up for case=%s already_paid=%s", last["case_id"], ans.already_paid)
    return True


def process_update(update: dict) -> None:
    msg = update.get("message")
    if not msg or (msg.get("chat") or {}).get("type") != "private":
        return  # groups/channels/edits are ignored
    chat_id = msg["chat"]["id"]
    mid = msg.get("message_id")
    sender = msg.get("from") or {}
    label = _label(sender)
    try:
        user, is_new = repo.upsert_tg_user(chat_id, label)
        text = msg.get("text") or ""
        parsed = _parse_command(text) if text else None

        first_time = is_new or not user.consent_at

        if parsed:
            reply = _handle_command(*parsed, user, chat_id, label)
            if reply:
                if first_time and reply is not WELCOME:
                    reply += "\n\n" + CONSENT
                telegram_bot.send_text(chat_id, reply, reply_to=mid)
                if first_time:
                    repo.set_consent(user.id)
                return

        # Follow-up to a recent verdict ("terus gimana?", "aku sudah transfer")? Answer in context.
        if text and not msg.get("forward_origin") and _handle_followup(text, user, chat_id, mid, label):
            return

        # Answer first, introduce ourselves after: a parent who forwards a scam wants the verdict.
        with telegram_bot.typing(chat_id):
            inp, input_type = _build_input(msg, user.id)
            if inp is None:
                telegram_bot.send_text(chat_id, "Maaf, jenis pesan ini belum bisa dicek. "
                                       "Coba teruskan teks, foto, pesan suara, atau video.", reply_to=mid)
                return
            case_id = repo.create_case(user.id, "tg", input_type, inp.text,
                                       channel_message_id=f"tg:{chat_id}:{mid}")
            if case_id is None:
                return
            result = analyze(inp)
            repo.save_verdict(case_id, result, input_text=result.extracted_text or inp.text)
            inp.media_bytes = None

        # Alert first, so the reply can say truthfully whether the guardian was told.
        status = notify.alert_guardians(user.id, label, result.verdict)
        if status == "sent":
            repo.mark_alerted(case_id)
        alerted = status == "sent"
        hint = "" if first_time else "/daftar_wali"  # the intro below already explains it
        telegram_bot.send_text(chat_id, format_reply(result.verdict, guardian_line(status, hint)), reply_to=mid)
        if first_time:
            telegram_bot.send_text(chat_id, FIRST_TIME_NOTE)
            repo.set_consent(user.id)

        log.info("tg case=%s risk=%s (muse=%s overrides=%s) tools=%s image=%s latency=%sms alerted=%s",
                 case_id, result.verdict.risk, result.model_risk, [o["rule"] for o in result.overrides],
                 result.tools_used, result.image_mode, result.latency_ms, alerted)
    except Exception:
        log.exception("processing telegram update %s failed", update.get("update_id"))
        try:
            telegram_bot.send_text(chat_id, "Maaf, ada gangguan saat mengecek. Sementara itu, "
                                   "jangan transfer atau kirim OTP dulu ya.", reply_to=mid)
        except Exception:
            pass
