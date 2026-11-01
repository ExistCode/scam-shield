"""Spot bank/e-wallet account numbers and phone numbers in a message (deterministic, no model).

Used to point the parent at the right official channel:
  account number -> flagged to the model (transfer to a personal account is a red flag)
  phone number   -> "Laporkan nomor ... di aduannomor.id" step on BAHAYA/WASPADA (see orchestrator._scam_numbers)
"""
from __future__ import annotations

import re

BANKS = r"(rek(?:ening)?|no\.?\s*rek|a\.?\s*n\.?|atas\s+nama|bca|bri|bni|mandiri|bsi|btn|cimb|permata|danamon|" \
        r"seabank|jago|blu|jenius|dana|ovo|gopay|shopeepay|linkaja)"
_DIGITS = r"(\d[\d\s.\-]{6,20}\d)"
_ACCOUNT = re.compile(r"\b" + BANKS + r"\b[^\d\n]{0,25}" + _DIGITS, re.I)
_EWALLETS = {"dana", "ovo", "gopay", "shopeepay", "linkaja"}
_PHONE = re.compile(r"(?<!\d)((?:\+?62|0)\s?8\d{1,2}[\s.\-]?\d{3,4}[\s.\-]?\d{3,5})(?!\d)")


def _clean(num: str) -> str:
    return re.sub(r"\D", "", num)


def extract_phones(text: str) -> list[str]:
    out: list[str] = []
    for m in _PHONE.findall(text or ""):
        d = _clean(m)
        d = "0" + d[2:] if d.startswith("62") else d
        if 10 <= len(d) <= 13 and d not in out:
            out.append(d)
    return out[:5]


def extract_accounts(text: str) -> list[str]:
    """Digit runs (8-16 digits) that follow a bank / e-wallet / 'rekening' / 'a.n.' keyword."""
    out: list[str] = []
    for m in _ACCOUNT.finditer(text or ""):
        d = _clean(m.group(2))
        looks_like_phone = bool(_PHONE.fullmatch(m.group(2).strip()))
        if looks_like_phone and m.group(1).lower() not in _EWALLETS:
            continue  # "a.n. Siska, WA 0857..." is a phone number, not an account
        if 8 <= len(d) <= 16 and d not in out:
            out.append(d)
    return out[:5]


def context_lines(text: str) -> list[str]:
    lines = []
    accounts = extract_accounts(text)
    if accounts:
        lines.append("Nomor rekening/e-wallet di pesan: " + ", ".join(accounts)
                     + " -> kalau ada permintaan transfer ke rekening pribadi, itu tanda bahaya.")
    phones = [p for p in extract_phones(text) if p not in accounts]
    if phones:
        lines.append("Nomor HP di pesan: " + ", ".join(phones)
                     + " -> kalau ini penipuan, sistem akan menambahkan cara melaporkan nomornya.")
    return lines
