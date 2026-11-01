"""Personal voice: talk like a caring child texting on WhatsApp, using the name the user chose.

Profile dict (see repo.get_profile): {"panggilan": "Mama" | None, "bahasa_style": "santai" | "sopan",
"contacts": [{"name": "Hans", "relation": "anak"}]}.
"""
from __future__ import annotations

import re

RELATIONS = {"a": "anak", "c": "cucu", "k": "keponakan", "s": "saudara", "m": "menantu", "p": "pasangan",
             "t": "teman", "l": "keluarga"}
RELATION_CODE = {v: k for k, v in RELATIONS.items()}


def panggilan(profile: dict | None) -> str | None:
    return (profile or {}).get("panggilan") or None


def is_sopan(profile: dict | None) -> bool:
    return (profile or {}).get("bahasa_style") == "sopan"


def family_names(profile: dict | None) -> list[str]:
    return [c["name"] for c in (profile or {}).get("contacts", []) if c.get("name")]


def join_names(names: list[str]) -> str:
    names = [n for n in names if n]
    if len(names) <= 1:
        return names[0] if names else ""
    return ", ".join(names[:-1]) + " dan " + names[-1]


# --------------------------------------------------------------------------- prompt block

def prompt_block(profile: dict | None) -> str:
    """Injected into the system prompts (verdict + follow-up)."""
    p = panggilan(profile)
    sopan = is_sopan(profile)
    contacts = (profile or {}).get("contacts") or []
    first = contacts[0]["name"] if contacts else None

    lines = ["PROFIL PENGGUNA DAN GAYA BICARA"]
    if p:
        lines.append(f'- Sapa pengguna HANYA dengan "{p}". JANGAN PERNAH pakai "Ibu/Bapak", "Ibu", "Bapak", '
                     f'"Bu", "Pak", "Anda", atau sapaan lain. Jangan gabungkan sapaan.')
    else:
        lines.append('- Panggilan pengguna belum diketahui: JANGAN pakai sapaan apa pun (tanpa "Ibu/Bapak", '
                     f'"Ibu", "Bapak", "Anda"). Kalau perlu kata ganti, pakai "{"Anda" if sopan else "kamu"}".')
    if contacts:
        fam = ", ".join(f"{c['name']} ({c.get('relation') or 'keluarga'})" for c in contacts)
        lines.append(f"- Keluarga yang terdaftar: {fam}. Kalau menyuruh cerita atau telepon keluarga, SEBUT "
                     f'NAMANYA, misalnya "Telepon {first} sekarang ya{", " + p if p else ""}." '
                     'Jangan tulis "anak atau keluarga".')
    else:
        lines.append('- Belum ada keluarga terdaftar: kalau perlu, tulis "telepon anak atau keluarga yang '
                     'dipercaya".')
    if sopan:
        lines.append('- Gaya SOPAN tapi hangat, seperti keponakan ke orang tua: kalimat pendek, bahasa '
                     'sehari-hari yang sopan ("saja", "ya", "dulu", "tidak", "sudah"), pakai "saya". '
                     "Bukan gaya surat resmi atau CS.")
    else:
        lines.append("- Gaya SANTAI, seperti anak atau keluarga dekat yang sayang dan lagi chat di WhatsApp: "
                     'kalimat pendek, bahasa sehari-hari ("aja", "ya", "dulu", "nggak", "udah", "kok"), '
                     'pakai "aku". Boleh 1 emoji.')
    lines.append('- JANGAN gaya CS/formal: tanpa "Terima kasih telah", "Mohon", "Silakan", "tersebut", '
                 '"dikarenakan", "dengan ini", dan tanpa label seperti "Alasannya:".')
    return "\n".join(lines)


def examples(profile: dict | None) -> dict[str, str]:
    """Example first sentences for reply_text, with the right address."""
    p = panggilan(profile)
    v = f", {p}" if p else ""
    if is_sopan(profile):
        return {"BAHAYA": f"Ini penipuan{v}. Jangan diikuti ya.",
                "WASPADA": f"Ini perlu dicek dulu{v}, jangan buru-buru ya.",
                "AMAN": f"Sepertinya aman{v}, tapi tetap hati-hati ya."}
    return {"BAHAYA": f"Ini penipuan{v}. Jangan diikutin ya.",
            "WASPADA": f"Ini dicek dulu ya{v}, jangan buru-buru.",
            "AMAN": f"Kayaknya aman{v}, tapi tetap hati-hati ya."}


# --------------------------------------------------------------------------- address safety net

_ADDR = r"(?:Ibu\s*/\s*Bapak|Bapak\s*/\s*Ibu|Ibu\s+atau\s+Bapak|Bapak\s+atau\s+Ibu)"


def address(text: str, profile: dict | None) -> str:
    """Never let "Ibu/Bapak" through: use the chosen panggilan, or drop the vocative if there is none."""
    if not text or not re.search(_ADDR, text, re.I):
        return text
    p = panggilan(profile)
    if p:
        return re.sub(_ADDR, p, text, flags=re.I)
    out = re.sub(rf",\s*{_ADDR}(?=\s*[.,!?]|\s*$)", "", text, flags=re.I)          # "..., Ibu/Bapak."
    out = re.sub(rf"(^|[.!?]\s+){_ADDR}\s*,\s*(\w)",                                # "Ibu/Bapak, ini..."
                 lambda m: m.group(1) + m.group(2).upper(), out, flags=re.I)
    return re.sub(_ADDR, "Anda" if is_sopan(profile) else "kamu", out, flags=re.I)


# --------------------------------------------------------------------------- onboarding

_NOT_A_NAME = re.compile(r"(?i)^(ok(e|ay)?|sip|siap|ya|iya|y|ga|gak|nggak|enggak|tidak|makasih|terima\s*kasih|"
                         r"thanks?|halo|hai|hi|hello|test|tes|apa|gimana|kenapa|beneran|serius|masa|oh|wah|aduh|"
                         r"duh|waduh|takut|terus|trus|jadi|lalu|udah|sudah|belum|baik|bagus|mantap|aman|bahaya|"
                         r"penipuan|hoaks|kok|loh|lho|hah|astaga|ya\s*ampun|alhamdulillah)$")
# Forms of address: right after a verdict we only accept answers that start with one of these
# (or a single plain word), so a follow-up like "terus gimana" isn't taken as a name.
_HONORIFIC = re.compile(r"(?i)^(mama|mami|mamah|mak|emak|ibu|bu|bunda|umi|papa|papi|bapak|pak|ayah|abah|abi|om|tante|"
                        r"oma|opa|nenek|kakek|eyang|uti|kung|mbah|bude|pakde|bulik|paklik|kak|kakak|mas|mbak|"
                        r"cik|koh|ci|ko|engkong|ama|teteh|aa|akang|uda|uni|ompung|inang)\b")
_LEADING = re.compile(r"(?i)^(panggil(\s+(aku|saya))?(\s+(aja|saja))?|panggilan(nya)?|nama(ku|\s+saya)?|"
                      r"aku|saya|dipanggil)\s*[:,]?\s+")


def parse_panggilan(text: str, strict: bool = False) -> str | None:
    """'Mama', 'panggil aja Bu Sri', 'Om Budi.' -> a clean panggilan, or None if it doesn't look like one.

    strict=True (just after a verdict, when the message could be a follow-up question): only accept a
    form of address ("Mama", "Bu Sri") or a single plain word ("Sri"), never a question."""
    t = (text or "").strip()
    if not t or len(t) > 40 or re.search(r"\d|https?://|www\.|@|\?", t) or t.startswith("/"):
        return None
    explicit = bool(_LEADING.match(t))  # "panggil aja ...", "nama saya ..."
    t = _LEADING.sub("", t).strip(" .!,~😊🙂🙏")
    words = t.split()
    if not words or len(words) > 3 or _NOT_A_NAME.match(t) or any(_NOT_A_NAME.match(w) for w in words):
        return None
    if strict and not (explicit or _HONORIFIC.match(t) or len(words) == 1):
        return None
    return " ".join(w[:1].upper() + w[1:] for w in words)


ASK_PANGGILAN = "Oh ya, mau aku panggil apa? (misalnya: Mama, Bu Sri, Om Budi)"


def panggilan_saved(p: str, register_hint: str, consent: str = "") -> str:
    """Reply to the onboarding answer. `consent` (the short privacy line) is added the first time only."""
    text = (f"Oke, mulai sekarang aku panggil *{p}* ya 😊\n"
            f"Kalau mau ganti, ketik {'/panggil' if register_hint.startswith('/') else 'panggil'} <nama>.\n"
            f"Biar keluarga ikut dikabarin kalau ada bahaya, ketik {register_hint}")
    return text + ("\n\n" + consent if consent else "")


# --------------------------------------------------------------------------- guardian line

def guardian_line(status: str | None, names: list[str] | None, profile: dict | None, register_hint: str = "") -> str:
    """Written from what actually happened to the alerts; "X udah aku kabarin" only if it was sent."""
    who = join_names(names or [])
    p = panggilan(profile)
    v = f", {p}" if p else ""
    sopan = is_sopan(profile)
    if status == "sent" and who:
        return f"✅ {who} sudah saya kabari{v}." if sopan else f"✅ {who} udah aku kabarin ya{v}."
    if status == "already" and who:
        return f"{who} sudah saya kabari tadi{v}." if sopan else f"{who} udah aku kabarin tadi ya{v}."
    if status == "failed":
        target = who or "keluarga"
        return (f"⚠️ Saya belum berhasil mengabari {target}. Tolong telepon {target} langsung ya{v}." if sopan
                else f"⚠️ Aku belum berhasil ngabarin {target}. Telepon {target} langsung ya{v}.")
    if status == "none" and register_hint:
        return (f"Belum ada keluarga yang terdaftar. Ketik {register_hint} supaya mereka ikut dikabari." if sopan
                else f"Belum ada keluarga yang didaftarin. Ketik {register_hint} biar mereka ikut dikabarin.")
    return ""
