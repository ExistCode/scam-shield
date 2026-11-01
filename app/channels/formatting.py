"""Channel-neutral reply formatting. Uses WhatsApp-style *bold* / _italic_; Telegram converts to HTML."""
import html
import re

from app.agent import persona
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


def guardian_line(status: str | None, register_hint: str = "", names: list[str] | None = None,
                  profile: dict | None = None) -> str:
    """From what actually happened to the alerts; "Hans udah aku kabarin" only if it was sent.
    status: "sent" | "already" (cooldown) | "failed" | "none" | "" (not BAHAYA)."""
    return persona.guardian_line(status, names, profile, register_hint)


def format_reply(v: Verdict, guardian: str = "", footer: str = "", profile: dict | None = None) -> str:
    """Emoji verdict line, one line of why, 2-3 numbered actions, official contacts, guardian line."""
    say = lambda t: persona.address((t or "").strip(), profile)  # noqa: E731
    lines = [f"{RISK_HEADER[v.risk]} · {say(v.reply_text)}", say(v.reason), ""]
    lines += [f"{i}. {say(s)}" for i, s in enumerate(v.next_steps[:4], 1)]  # Muse's steps + report/check steps
    lines += [f"ℹ️ {line}" for line in v.lookups]
    if v.contacts:
        lines += [format_contacts(v.contacts)]
    if guardian:
        lines += ["", guardian]
    if footer:
        lines += ["", footer]
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
    if result.provider == "wonder":  # Spark was unsure, Muse Wonder gave the final answer
        model = "Muse Spark → Muse Wonder (cek ulang)"
    elif result.provider == "meta":  # name the model that actually answered (MUSE_MODEL can be Wonder)
        model = "Muse Wonder" if (result.model or "").startswith("muse-wonder") else "Muse Spark"
    elif result.model:
        model = f"{result.model.rsplit('/', 1)[-1]} (cadangan)"
    else:
        model = ""
    if model:
        if used & {"muse_vision", "describe_image"}:
            model += " (baca gambar)"
        steps.append(model)
    for tool, label in (("sam_crop", "SAM 3.1 (cari QR)"), ("decode_qris", "baca QRIS"), ("check_link", "cek link"), ("safe_word_check", "cek kata aman"),
                        ("aduannomor_lookup", "cek AduanNomor")):
        if tool in used:
            steps.append(label)
    if any(o.get("field") == "risk" for o in result.overrides):
        steps.append("aturan keamanan")
    return ("🔎 _Dicek dengan: " + " → ".join(steps) + "_") if steps else ""


def guardian_alert_text(parent_label: str, v: Verdict) -> str:
    category = CATEGORY_LABEL.get(v.category, v.category)
    if v.money_sent:  # "aku udah transfer": it's no longer "before any money is sent"
        return (
            f"🚨 *Darurat Cek Dulu*\n\n{parent_label} bilang mungkin *sudah terlanjur* transfer, kirim kode, "
            f"atau pasang aplikasi ({category}).\n\nTelepon beliau sekarang dan bantu telepon call center "
            "bank untuk blokir, lalu lapor secepatnya (sebelum 12 jam) supaya dananya bisa diblokir."
        )
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
