"""SAM 3.1 (Meta Model API): find an object by a short phrase and crop it out of a photo.

Used by decode_qris when the local QR scan misses a code in a big, busy photo (a small QRIS sticker on
a warung counter): SAM finds the "QR code", we crop it with some margin and decode the crop.

API (dev.meta.ai/docs/sam): POST /v1/responses, model "sam-3.1", one input_text noun phrase plus one
input_image. The answer is a text grammar, one record per match:
    <0f>0<|box;x1=126;y1=335;x2=634;y2=845;w=760;h=1080;c=0.95|><|mask;...|>
Boxes are xyxy in source pixels; w/h are the size of the image we sent.
"""
from __future__ import annotations

import base64
import io
import logging
import re

import httpx
from PIL import Image

from app.config import settings

log = logging.getLogger("cekdulu.sam")

MODEL = "sam-3.1"
MAX_SIDE = 2048  # downscale big photos before sending; boxes are scaled back to the original
_BOX = re.compile(r"<\|box;([^|]*)\|>")


def parse_boxes(text: str) -> list[dict]:
    out = []
    for m in _BOX.finditer(text or ""):
        kv = dict(part.split("=", 1) for part in m.group(1).split(";") if "=" in part)
        try:
            box = {k: float(kv[k]) for k in ("x1", "y1", "x2", "y2", "w", "h")}
        except (KeyError, ValueError):
            continue
        box["c"] = float(kv.get("c", 1.0))
        out.append(box)
    return sorted(out, key=lambda b: -b["c"])


def find_boxes(image_bytes: bytes, phrase: str, min_conf: float = 0.3, timeout: float = 8.0) -> list[dict]:
    """Boxes (in original-image pixels) for `phrase`, best first. Raises on API errors."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    scale = min(1.0, MAX_SIDE / max(img.size))
    small = img.resize((round(img.width * scale), round(img.height * scale))) if scale < 1 else img
    buf = io.BytesIO()
    small.save(buf, "JPEG", quality=90)
    body = {
        "model": MODEL,
        "input": [{"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": phrase},
            {"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()},
        ]}],
        "metadata": {"include_confidence": "true", "mask_encoding": "one_bit"},
        "stream": False,
    }
    r = httpx.post(f"{settings.meta_base_url}/responses", json=body, timeout=timeout,
                   headers={"Authorization": f"Bearer {settings.meta_api_key}"})
    if r.status_code != 200:
        raise RuntimeError(f"sam-3.1 failed: {r.status_code}")
    text = "".join(c.get("text", "") for o in r.json().get("output", []) for c in (o.get("content") or [])
                   if c.get("type") == "output_text")
    boxes = []
    for b in parse_boxes(text):
        if b["c"] < min_conf:
            continue
        fx, fy = img.width / b["w"], img.height / b["h"]  # back to original pixels
        boxes.append({"x1": b["x1"] * fx, "y1": b["y1"] * fy, "x2": b["x2"] * fx, "y2": b["y2"] * fy, "c": b["c"]})
    return boxes


def crop(image_bytes: bytes, box: dict, pad: float = 0.15, min_side: int = 600) -> bytes:
    """Crop the box with a margin and upscale small crops (QR decoders like big, sharp codes)."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    bw, bh = box["x2"] - box["x1"], box["y2"] - box["y1"]
    x1 = max(0, int(box["x1"] - bw * pad))
    y1 = max(0, int(box["y1"] - bh * pad))
    x2 = min(img.width, int(box["x2"] + bw * pad))
    y2 = min(img.height, int(box["y2"] + bh * pad))
    out = img.crop((x1, y1, x2, y2))
    if max(out.size) < min_side:
        f = min_side / max(out.size)
        out = out.resize((round(out.width * f), round(out.height * f)), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, "PNG")
    return buf.getvalue()
