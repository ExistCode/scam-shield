"""Optional public lookup: has this phone number been reported on AduanNomor.id (Komdigi)?

This is the same undocumented endpoint the site's own "cek nomor" form calls. It is NOT an official
API or a partnership, so it is strictly an extra:
- 3 s timeout, cached per number, any failure is silent (the reply still says "laporkan di aduannomor.id").
- Only `reported_count` (and the crime types, for logs) are used. `reported_phone_status` is a
  placeholder string on their side and is ignored.
- If the live call fails, a response captured earlier (app/data/aduannomor_fixtures.json) is used if one
  exists for that number, so the demo still works if the server's IP gets blocked.

Env: ADUANNOMOR_MODE = live (default) | fixture (never call the site) | off.

Capture a real response for the demo:
    .venv/bin/python -m app.tools.aduannomor capture 0857xxxxxxxx
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path

import httpx

log = logging.getLogger("cekdulu.aduannomor")

URL = "https://aduannomor.id/api/v.1/ticket/phone-check"
PAGE = "https://aduannomor.id/cek-nomor-seluler"
TIMEOUT = 3.0
CACHE_TTL = 6 * 3600
FIXTURES = Path(os.environ.get("ADUANNOMOR_FIXTURES", Path(__file__).resolve().parents[1] / "data" / "aduannomor_fixtures.json"))
HEADERS = {
    # The form rejects requests without these (403). We identify ourselves honestly in the User-Agent.
    "Origin": "https://aduannomor.id",
    "Referer": PAGE,
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (compatible; Ronda/1.0; +https://github.com/ExistCode/scam-shield)",
}

_cache: dict[str, tuple[float, dict | None]] = {}
_lock = threading.Lock()


def _mode() -> str:
    return os.environ.get("ADUANNOMOR_MODE", "live").strip().lower()


def to_e164(number: str) -> str | None:
    """'0857-1122-3344' / '+62 857...' / '857...' -> '+6285711223344'."""
    d = re.sub(r"\D", "", number or "")
    if d.startswith("62"):
        d = d[2:]
    d = d.lstrip("0")
    return f"+62{d}" if re.fullmatch(r"8\d{8,11}", d) else None


def parse(body: dict) -> dict | None:
    """Keep only what we use. Returns {"reported_count": int, "kejahatan": [{"name", "count"}]} or None."""
    data = (body or {}).get("data")
    if not isinstance(data, dict) or body.get("error"):
        return None
    try:
        count = int(data.get("reported_count") or 0)
    except (TypeError, ValueError):
        return None
    kinds = []
    for item in data.get("reported_kejahatan") or []:
        if isinstance(item, dict):
            name = next((str(v) for k, v in item.items() if isinstance(v, str)), None)
            n = next((v for k, v in item.items() if isinstance(v, int) and not isinstance(v, bool)), None)
            if name:
                kinds.append({"name": name, "count": n})
        elif isinstance(item, str):
            kinds.append({"name": item, "count": None})
    return {"reported_count": max(count, 0), "kejahatan": kinds}


def _fixture(e164: str) -> dict | None:
    try:
        raw = json.loads(FIXTURES.read_text()).get(e164)
    except (OSError, ValueError):
        return None
    out = parse(raw) if raw else None
    if out:
        out["source"] = "fixture"
    return out


def _live(e164: str) -> dict | None:
    r = httpx.post(URL, data={"calling_code": "+62", "phone_number": e164[3:]}, headers=HEADERS, timeout=TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"status {r.status_code}")
    out = parse(r.json())
    if out is None:
        raise RuntimeError("unexpected response")
    out["source"] = "live"
    return out


def check_phone(number: str) -> dict | None:
    """{"reported_count", "kejahatan", "source"} or None. Never raises."""
    mode = _mode()
    e164 = to_e164(number)
    if mode == "off" or not e164:
        return None
    now = time.monotonic()
    with _lock:
        hit = _cache.get(e164)
        if hit and now - hit[0] < CACHE_TTL:
            return hit[1]
    result = None
    if mode != "fixture":
        try:
            result = _live(e164)
        except Exception as exc:  # timeout, 403, IP block, schema change: all silent
            log.info("aduannomor lookup failed for %s: %s", e164[:6] + "…", exc)
    if result is None:
        result = _fixture(e164)
    with _lock:
        _cache[e164] = (now, result)
    return result


def check_many(numbers: list[str], budget: float = TIMEOUT + 0.5) -> dict[str, dict]:
    """Look up several numbers in parallel within one time budget. Missing = failed/timed out."""
    if not numbers or _mode() == "off":
        return {}
    pool = ThreadPoolExecutor(max_workers=min(3, len(numbers)))
    futures = {pool.submit(check_phone, n): n for n in numbers}
    done, _ = wait(futures, timeout=budget)
    pool.shutdown(wait=False, cancel_futures=True)
    return {futures[f]: f.result() for f in done if f.result() is not None}


def capture(number: str) -> None:
    e164 = to_e164(number)
    if not e164:
        sys.exit("not an Indonesian mobile number")
    r = httpx.post(URL, data={"calling_code": "+62", "phone_number": e164[3:]}, headers=HEADERS, timeout=10)
    r.raise_for_status()
    body = r.json()
    body["_captured"] = time.strftime("%Y-%m-%d")
    data = json.loads(FIXTURES.read_text()) if FIXTURES.exists() else {}
    data[e164] = body
    FIXTURES.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print(e164, "reported_count =", (parse(body) or {}).get("reported_count"), "->", FIXTURES)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "capture":
        capture(sys.argv[2])
    else:
        print(__doc__)
