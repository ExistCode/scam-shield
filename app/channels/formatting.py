"""Channel-neutral reply formatting. Uses WhatsApp-style *bold* / _italic_; Telegram converts to HTML."""
import html
import re

from app.agent.contacts import format_contacts
from app.agent.schema import AnalysisResult, Verdict

RISK_HEADER = {
    "AMAN": "🟢 *KEMUNGKINAN AMAN*",
    "WASPADA": "🟡 *WASPADA*",
    "BAHAYA": "🔴 *BAHAYA*",
}

CATEGORY_LABEL = {
    "kurir_apk": "aplikasi kurir palsu (APK)",
    "minta_pulsa": "mengaku keluarga/teman minta uang",
    "cs_bank_palsu": "CS bank palsu",
    "aparat_palsu": "polisi/pejabat palsu",
    "investasi": "investasi bodong",
    "belanja_online": "toko online palsu",
    "lowongan_kerja": "lowongan kerja palsu",
    "hadiah_palsu": "hadiah/undian palsu",
    "pinjol": "pinjaman online palsu",
    "qris_palsu": "QRIS palsu",
    "hoaks": "hoaks",
    "lainnya": "pesan mencurigakan",
}


def guardian_line(status: str | None, register_hint: str = "", names: list[str] | None = None) -> str:
    """Written from what actually happened to the guardian alerts. Never claims an alert that wasn't sent.

    status: "sent" | "already" (cooldown: alerted a few minutes ago) | "failed" | "none" | "" (not BAHAYA)
    """
    who = ", ".join(names or []) or "wali Ibu/Bapak"
    if status == "sent":
        return f"✅ Sudah kami kabari: {who}"
    if status == "already":
        return f"✅ Sudah kami kabari beberapa menit lalu: {who}"
    if status == "failed":
        return f"⚠️ Pesan ke {who} belum terkirim. Tolong telepon mereka langsung ya."
    if status == "none" and register_hint:
        return f"Belum ada wali terdaftar, ketik {register_hint}"
    return ""


def format_reply(v: Verdict, guardian: str = "", footer: str = "") -> str:
    steps = "\n".join(f"{i}. {s}" for i, s in enumerate(v.next_steps, 1))
    lines = [RISK_HEADER[v.risk], "", v.reply_text.strip(), "", f"Alasannya: {v.reason.strip()}", "",
             "*Yang perlu dilakukan:*", steps]
    if v.contacts:
        lines += ["", format_contacts(v.contacts)]
    if guardian:
        lines += ["", guardian]
    lines += ["", "_Cek dulu, baru transfer._"]
    if footer:
        lines += [footer]
    return "\n".join(lines)


def working_text(input_type: str) -> str:
    """Shown right away while the check runs (Telegram edits it into the verdict)."""
    if input_type in ("audio", "video"):
        return "🎧 Sedang mendengarkan dan mengecek pesannya…"
    if input_type == "image":
        return "🔎 Sedang memeriksa gambarnya…"
    return "🔎 Sedang dicek…"


def tools_footer(result: AnalysisResult) -> str:
    """One line showing what actually ran, e.g. "Dicek dengan: Muse Voice → Muse Spark → cek link"."""
    used = set(result.tools_used)
    steps = []
    if "transcribe_audio" in used:
        steps.append("Muse Voice")
    if result.provider == "meta":
        model = "Muse Spark"
    elif result.model:
        model = f"{result.model.rsplit('/', 1)[-1]} (cadangan)"
    else:
        model = ""
    if model:
        if used & {"muse_vision", "describe_image"}:
            model += " (baca gambar)"
        steps.append(model)
    for tool, label in (("decode_qris", "baca QRIS"), ("check_link", "cek link"), ("safe_word_check", "cek kata aman")):
        if tool in used:
            steps.append(label)
    if any(o.get("field") == "risk" for o in result.overrides):
        steps.append("aturan keamanan")
    return ("🔎 _Dicek dengan: " + " → ".join(steps) + "_") if steps else ""


def guardian_alert_text(parent_label: str, v: Verdict) -> str:
    category = CATEGORY_LABEL.get(v.category, v.category)
    return (
        f"🔴 *Peringatan Cek Dulu*\n\n{parent_label} baru saja menerima pesan yang terdeteksi "
        f"*BAHAYA* ({category}).\nAlasan: {v.reason}\n\nMohon hubungi beliau sebelum ada uang yang ditransfer."
    )


_BOLD = re.compile(r"\*([^*\n]+)\*")
_ITALIC = re.compile(r"(?<![\w])_([^_\n]+)_(?![\w])")


def to_telegram_html(text: str) -> str:
    """Escape everything (model output included), then turn *x* / _x_ into <b>/<i>."""
    out = html.escape(text, quote=False)
    out = _BOLD.sub(r"<b>\1</b>", out)
    return _ITALIC.sub(r"<i>\1</i>", out)
