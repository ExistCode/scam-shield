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
    if input_type == "video":
        return "🎬 Lagi nonton videonya…"
    if input_type == "audio":
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
    if result.provider == "wonder":  # escalated: Wonder wrote the final verdict from Spark's tool results
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
    for tool, label in (("segment_region", "SAM 3.1 (potong area)"), ("decode_qris", "baca QRIS"), ("check_link", "cek link"), ("safe_word_check", "cek kata aman"),
                        ("aduannomor_lookup", "cek AduanNomor")):
        if tool in used:
            steps.append(label)
    if any(o.get("field") == "risk" for o in result.overrides):
        steps.append("aturan keamanan")
    return ("🔎 _Dicek dengan: " + " → ".join(steps) + "_") if steps else ""


# What the parent got, in words a family member would use. Keyed by scam_type first, then category.
ALERT_PHRASE = {
    "impersonation_family": "chat dari nomor baru yang ngaku keluarga, minta transfer darurat",
    "minta_pulsa": "chat dari nomor baru yang ngaku keluarga, minta transfer darurat",
    "fake_police": "telepon atau chat yang ngaku polisi, nakut-nakutin terus minta transfer",
    "aparat_palsu": "telepon atau chat yang ngaku polisi, nakut-nakutin terus minta transfer",
    "bank_otp": "chat yang ngaku dari bank, minta kode OTP atau isi data rekening",
    "cs_bank_palsu": "chat yang ngaku dari bank, minta kode OTP atau isi data rekening",
    "kurir_apk": "file aplikasi (.apk) yang nyamar jadi resi paket atau undangan",
    "investasi": "ajakan investasi yang janji untung gede terus minta transfer",
    "investment": "ajakan investasi yang janji untung gede terus minta transfer",
    "belanja_online": "tawaran barang murah yang minta transfer ke rekening pribadi",
    "lowongan_kerja": "tawaran kerja online yang ujung-ujungnya minta deposit",
    "hadiah_palsu": "kabar menang undian yang minta bayar pajak hadiah dulu",
    "pinjol": "tawaran pinjaman yang minta biaya admin di depan",
    "qris_palsu": "QRIS yang nama penerimanya nggak cocok sama tokonya",
    "hoaks": "pesan berantai yang nyuruh klik link atau kasih data",
    "lainnya": "pesan yang mencurigakan",
}


def alert_phrase(v: Verdict) -> str:
    if v.scam_type == "impersonation" and v.category in ("minta_pulsa", "lainnya"):
        return ALERT_PHRASE["impersonation_family"]
    return ALERT_PHRASE.get(v.category) if v.category != "lainnya" else \
        ALERT_PHRASE.get(v.scam_type, ALERT_PHRASE["lainnya"])


def guardian_alert_text(guardian: str, panggilan: str, v: Verdict) -> str:
    """Two lines at most, like a family member texting: who, what they got, what to do."""
    g = guardian or "Halo"
    p = panggilan or "keluargamu"
    phrase = alert_phrase(v) or ALERT_PHRASE["lainnya"]
    if v.money_sent:  # "aku udah transfer": no longer "before any transfer"
        return (f"🚨 {g}, {p} bilang udah terlanjur transfer atau kirim kode gara-gara {phrase}.\n"
                f"Telepon {p} sekarang ya, bantu telepon bank buat blokir.")
    return f"🔴 {g}, {p} barusan dapet {phrase}. Kayaknya penipuan. Telepon {p} sekarang ya."


_BOLD = re.compile(r"\*([^*\n]+)\*")
_ITALIC = re.compile(r"(?<![\w])_([^_\n]+)_(?![\w])")


def to_telegram_html(text: str) -> str:
    """Escape everything (model output included), then turn *x* / _x_ into <b>/<i>."""
    out = html.escape(text, quote=False)
    out = _BOLD.sub(r"<b>\1</b>", out)
    return _ITALIC.sub(r"<i>\1</i>", out)


# --------------------------------------------------------------------------- "🔎 Detail" trace (Telegram HTML)

_RULE_TEXT = {
    "apk_selalu_bahaya": "file .apk selalu BAHAYA",
    "safe_browsing_hit": "Google Safe Browsing menandai link-nya",
    "qris_crc_tidak_valid": "checksum QRIS tidak valid, tidak boleh AMAN",
    "aman_tapi_ragu": "AMAN tapi keyakinan rendah jadi WASPADA",
    "teks_disesuaikan_risiko": "teks jawaban disesuaikan dengan risiko baru",
    "larang_100_persen_aman": 'kata "100% aman" dihapus',
    "hapus_jangan_khawatir": '"jangan khawatir" dihapus dari peringatan',
    "langkah_apk_terpasang": 'tambah langkah "kalau sudah terpasang"',
    "nomor_tidak_resmi_dihapus": "nomor yang bukan kontak resmi dihapus",
    "lapor_nomor_penipu": "tambah langkah lapor nomor di aduannomor.id",
    "cek_rekening": "tambah langkah cek rekening di cekrekening.id",
    "qris_dibaca_otomatis": "QR dibaca otomatis (Muse tidak memanggil decode_qris)",
}


def _model_name(model: str | None) -> str:
    m = model or ""
    if m.startswith("muse-wonder"):
        return f"Muse Wonder ({m})"
    if m.startswith("muse-spark"):
        return f"Muse Spark ({m})"
    return m or "?"


def trace_html(check: dict) -> str:
    """Compact trace of one check for the "🔎 Detail" button. Every dynamic value is HTML-escaped."""
    e = lambda x: html.escape(str(x), quote=False)  # noqa: E731
    d = check.get("details") or {}
    conf = float(check.get("confidence") or 0)
    category = CATEGORY_LABEL.get(check["category"], check["category"])
    lines = [f"<b>🔎 Detail pengecekan #{e(check['case_id'])}</b>",
             f"Hasil: <b>{e(check['risk'])}</b> · {e(category)} · keyakinan {conf:.2f}"]

    steps = d.get("trace") or []
    lines += ["", "<b>Langkah</b>"]
    if steps:
        for i, st in enumerate(steps, 1):
            lines.append(f"{i}. <b>{e(st.get('step'))}</b> ({e(st.get('input'))}) → {e(st.get('result'))}")
    else:
        lines.append("Muse menilai dari teksnya saja (tanpa tool).")

    esc = d.get("escalation")
    if esc and esc.get("spark"):
        sp, wo = esc["spark"], esc.get("wonder")
        model = (f"{e(_model_name(sp.get('model')))}: {e(sp['risk'])} {e(sp['confidence'])} → "
                 + (f"{e(_model_name(check.get('model')))}: {e(wo['risk'])} {e(wo['confidence'])} (cek ulang)" if wo
                    else "Muse Wonder gagal, jawaban Spark dipakai"))
    else:
        model = e(_model_name(check.get("model")))
    lines += ["", f"<b>Model</b>: {model}"]

    overrides = d.get("overrides") or []
    lines += ["", "<b>Aturan keamanan</b>"]
    if overrides:
        for o in overrides:
            why = _RULE_TEXT.get(o.get("rule"), o.get("rule"))
            if o.get("field") == "risk":
                lines.append(f"• Muse bilang {e(o.get('from'))} → aturan <code>{e(o.get('rule'))}</code> ubah jadi "
                             f"<b>{e(o.get('to'))}</b> ({e(why)})")
            else:
                lines.append(f"• <code>{e(o.get('rule'))}</code>: {e(why)}")
    else:
        lines.append("Tidak ada. Jawaban Muse dipakai apa adanya.")

    extra = []
    if check.get("guardian_alerted"):
        extra.append("wali dikabari")
    if check.get("latency_ms"):
        extra.append(f"{check['latency_ms'] / 1000:.1f} dtk")
    if extra:
        lines += ["", e(" · ".join(extra))]
    return "\n".join(lines)[:4000]
