"""Official contacts: the ONLY phone numbers and sites Cek Dulu ever shows.

The model never writes contact details. It outputs `scam_type` (and `money_sent`); the code appends
contacts from this map, so a number can't be hallucinated. Any other phone/short-code/account number
in model-written text is removed by `scrub_numbers`.

Only verified entries live here.
TODO: add other banks' call centers (Mandiri, BRI, BNI, BSI, ...) only after verifying each number on
the bank's official website. Do not add numbers from memory.
"""
from __future__ import annotations

import re
from typing import Literal

ScamType = Literal["fake_police", "impersonation", "bank_otp", "investment", "pinjol", "other"]
SCAM_TYPES: list[str] = ["fake_police", "impersonation", "bank_otp", "investment", "pinjol", "other"]

POLICE = {
    "name": "Polisi",
    "contact": "110",
    "when": "Ada yang mengaku polisi/aparat atau keluarga dan minta uang: tutup telepon, lalu telepon 110.",
    "note": "",
}
IASC = {
    "name": "IASC (Indonesia Anti-Scam Centre, OJK)",
    "contact": "https://iasc.ojk.go.id",
    "when": "Sudah terlanjur transfer: lapor SECEPATNYA, sebaiknya kurang dari 12 jam, supaya dana bisa diblokir.",
    "note": "lapor secepatnya, sebelum 12 jam, biar dananya bisa diblokir",
}
OJK = {
    "name": "Kontak OJK",
    "contact": "157 / WhatsApp 081-157-157-157",
    "when": "Tanyakan apakah investasi/pinjaman ini terdaftar dan berizin di OJK.",
    "note": "cek izinnya di sini",
}
ADUAN_NOMOR = {
    "name": "Lapor nomor penipu",
    "contact": "https://aduannomor.id",
    "when": "Laporkan nomor HP yang dipakai penipu.",
    "note": "",
}
HALO_BCA = {
    "name": "Halo BCA",
    "contact": "1500888",
    "when": "Nasabah BCA: telepon untuk blokir rekening/kartu. Bank lain: telepon nomor di balik kartu ATM Anda.",
    "note": "bank lain: nomor di balik kartu ATM",
}

# scam type (or situation) -> contacts, in the order they should be shown
CONTACTS_BY_TYPE: dict[str, list[dict]] = {
    "fake_police": [POLICE],
    "impersonation": [POLICE],
    "bank_otp": [HALO_BCA, IASC],
    "investment": [OJK],
    "pinjol": [OJK],
    "money_sent": [HALO_BCA, IASC],  # transfer / OTP / install already happened: bank first, then IASC
    "scam_phone_number": [ADUAN_NOMOR],
}

# Fallback when the model says scam_type="other" but the category is specific.
_TYPE_FROM_CATEGORY = {
    "aparat_palsu": "fake_police",
    "minta_pulsa": "impersonation",
    "cs_bank_palsu": "bank_otp",
    "investasi": "investment",
    "pinjol": "pinjol",
}


def resolve_type(scam_type: str | None, category: str | None) -> str:
    if scam_type in SCAM_TYPES and scam_type != "other":
        return scam_type
    return _TYPE_FROM_CATEGORY.get(category or "", "other")


def contacts_for(scam_type: str, money_sent: bool = False, scam_phone: bool = False) -> list[dict]:
    keys = (["money_sent"] if money_sent else []) + [scam_type] + (["scam_phone_number"] if scam_phone else [])
    out: list[dict] = []
    for key in keys:
        for c in CONTACTS_BY_TYPE.get(key, []):
            if c not in out:
                out.append(c)
    return out


def format_contacts(contacts: list[dict]) -> str:
    """One short line per contact, e.g. "📞 Polisi: *110*"."""
    return "\n".join(f"📞 {c['name']}: *{c['contact']}*" + (f" ({c['note']})" if c.get("note") else "")
                     for c in contacts)


# --------------------------------------------------------------------------- number allowlist

def _digits(s: str) -> str:
    d = re.sub(r"\D", "", s)
    return "0" + d[2:] if d.startswith("62") and len(d) > 9 else d


ALLOWED_NUMBERS = frozenset(
    _digits(part)
    for c in (POLICE, IASC, OJK, ADUAN_NOMOR, HALO_BCA)
    for part in re.findall(r"\+?\d[\d\- ]*\d", c["contact"])
)

# Candidate numbers: digit runs that may contain spaces/dashes (0812-3456-7890, +62 812 ...).
_NUM = re.compile(r"(?<![\w.,/])\+?\d(?:[\d\- ]*\d)?(?![\w%])")
_CONTACT_CONTEXT = re.compile(r"(?i)(telepon|telpon|telp|hubungi|call|nomor|no\.|wa|whatsapp|sms|hotline|"
                              r"layanan|kontak|cs|halo|dial|tekan)\W{0,3}(\w+\W{1,3}){0,2}$")


def _is_contact_number(text: str, m: re.Match) -> bool:
    raw = m.group(0)
    d = _digits(raw)
    after = text[m.end():m.end() + 2]
    if re.match(r"[.,]\d", after):  # 1.500.000 / 2,5 -> an amount, not a number to call
        return False
    if text[max(0, m.start() - 3):m.start()].lower().strip().endswith("rp"):
        return False
    if d.startswith("0") and 9 <= len(d) <= 14:  # mobile / landline
        return True
    if len(d) >= 8:  # account numbers and other long ids
        return True
    if d.startswith("1") and len(d) in (3, 5, 6, 7):  # short codes like 110, 14000, 1500888
        return bool(_CONTACT_CONTEXT.search(text[max(0, m.start() - 30):m.start()]))
    return False


def unofficial_numbers(text: str) -> list[str]:
    """Contact-like numbers in `text` that are not in this file."""
    return [m.group(0) for m in _NUM.finditer(text or "")
            if _is_contact_number(text, m) and _digits(m.group(0)) not in ALLOWED_NUMBERS]


def scrub_numbers(text: str) -> str:
    """Remove any contact-like number that isn't an official one from model-written text."""
    def repl(m: re.Match) -> str:
        if _is_contact_number(text, m) and _digits(m.group(0)) not in ALLOWED_NUMBERS:
            return "(nomor disembunyikan)"
        return m.group(0)
    return _NUM.sub(repl, text or "")
