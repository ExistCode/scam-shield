"""Channel-neutral reply formatting. Uses WhatsApp-style *bold* / _italic_; Telegram converts to HTML."""
import html
import re

from app.agent.schema import Verdict

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


def format_reply(v: Verdict) -> str:
    steps = "\n".join(f"{i}. {s}" for i, s in enumerate(v.next_steps, 1))
    lines = [RISK_HEADER[v.risk], "", v.reply_text.strip(), "", f"Alasannya: {v.reason.strip()}", "",
             "*Yang perlu dilakukan:*", steps]
    if v.alert_guardian:
        lines += ["", "Kami juga sudah kabari wali Ibu/Bapak (kalau terdaftar)."]
    lines += ["", "_Cek dulu, baru transfer._"]
    return "\n".join(lines)


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
