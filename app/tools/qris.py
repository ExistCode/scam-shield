"""decode_qris: decode a QR from an image, parse EMVCo TLV (QRIS), verify CRC16.

Deterministic, no model involved.
"""
from __future__ import annotations

import cv2
import numpy as np

# Common QRIS / EMVCo merchant-presented-mode tags
TAG_NAMES = {
    "00": "payload_format",
    "01": "point_of_initiation",  # 11 static, 12 dynamic
    "52": "mcc",
    "53": "currency",
    "54": "amount",
    "58": "country",
    "59": "merchant_name",
    "60": "merchant_city",
    "61": "postal_code",
    "62": "additional_data",
    "63": "crc",
}


def crc16_ccitt(data: str) -> str:
    """CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF), as used by EMVCo QR."""
    crc = 0xFFFF
    for byte in data.encode("utf-8"):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return f"{crc:04X}"


def parse_tlv(payload: str) -> dict[str, str]:
    """Parse a flat EMVCo TLV string into {tag: value}. Raises ValueError on malformed input."""
    out: dict[str, str] = {}
    i = 0
    while i < len(payload):
        if i + 4 > len(payload):
            raise ValueError("truncated TLV header")
        tag, length_str = payload[i : i + 2], payload[i + 2 : i + 4]
        if not length_str.isdigit():
            raise ValueError(f"bad length at {i}")
        length = int(length_str)
        value = payload[i + 4 : i + 4 + length]
        if len(value) != length:
            raise ValueError("truncated TLV value")
        out[tag] = value
        i += 4 + length
    return out


def parse_qris(payload: str) -> dict:
    """Parse a QRIS payload string into merchant fields + validity flags."""
    result: dict = {
        "is_emv": False,
        "merchant_name": None,
        "merchant_city": None,
        "nmid": None,
        "acquirer": None,
        "dynamic": None,
        "amount": None,
        "crc_valid": False,
        "flags": [],
    }
    try:
        tags = parse_tlv(payload)
    except ValueError as exc:
        result["flags"].append(f"format_rusak: {exc}")
        return result

    result["is_emv"] = tags.get("00") == "01"
    if not result["is_emv"]:
        result["flags"].append("bukan_format_qris")

    result["merchant_name"] = tags.get("59")
    result["merchant_city"] = tags.get("60")
    result["amount"] = tags.get("54")
    if "01" in tags:
        result["dynamic"] = tags["01"] == "12"

    # Merchant account info lives in tags 26..51; QRIS national (ID.CO.QRIS.WWW) carries NMID in sub-tag 02.
    for tag in sorted(tags):
        if tag.isdigit() and 26 <= int(tag) <= 51:
            try:
                sub = parse_tlv(tags[tag])
            except ValueError:
                continue
            guid = sub.get("00", "")
            if "QRIS" in guid.upper() and sub.get("02"):
                result["nmid"] = sub["02"]
            elif guid and not result["acquirer"]:
                result["acquirer"] = guid

    crc = tags.get("63")
    if crc and payload.endswith(crc) and len(crc) == 4:
        result["crc_valid"] = crc16_ccitt(payload[:-4]).upper() == crc.upper()
    if not result["crc_valid"]:
        result["flags"].append("crc_tidak_valid")
    if result["is_emv"] and tags.get("58") not in (None, "ID"):
        result["flags"].append(f"negara_bukan_indonesia:{tags.get('58')}")
    if result["is_emv"] and not result["nmid"]:
        result["flags"].append("nmid_tidak_ada")
    return result


def decode_qr_image(image_bytes: bytes) -> str | None:
    """Return the decoded QR text, or None if no QR found."""
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return None
    # Two detectors: the classic one misses some codes (finder-pattern detection), the Aruco-based one
    # catches most of those. Try both on several versions of the image (photos are often small/blurry).
    detectors = [cv2.QRCodeDetector()]
    if hasattr(cv2, "QRCodeDetectorAruco"):
        detectors.append(cv2.QRCodeDetectorAruco())
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    candidates = [gray]
    h, w = gray.shape[:2]
    if max(h, w) < 1200:
        candidates.append(cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC))
    elif max(h, w) > 2400:  # big phone photos: downscale helps both detectors
        candidates.append(cv2.resize(gray, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA))
    candidates.append(cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1])
    for detector in detectors:
        for cand in candidates:
            try:
                data, _, _ = detector.detectAndDecode(cand)
            except cv2.error:
                continue
            if data:
                return data
    return None


def decode_qris(image_bytes: bytes) -> dict | None:
    """Tool entry point. Returns None when the image contains no QR code."""
    raw = decode_qr_image(image_bytes)
    if raw is None:
        return None
    parsed = parse_qris(raw)
    parsed["raw"] = raw[:300]
    return parsed


def summarize(q: dict) -> str:
    parts = [
        f"merchant_name={q.get('merchant_name')!r}",
        f"kota={q.get('merchant_city')!r}",
        f"nmid={q.get('nmid')!r}",
        f"crc_valid={q.get('crc_valid')}",
    ]
    if q.get("amount"):
        parts.append(f"nominal={q['amount']}")
    if q.get("flags"):
        parts.append("flags=" + ",".join(q["flags"]))
    return "QR terbaca: " + "; ".join(parts)
