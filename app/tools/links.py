"""check_link: expand short URLs (redirect follow) and flag risky domains.

Redirects are followed manually, hop by hop, refusing private/loopback addresses (SSRF guard).
Reputation = local heuristics + Google Safe Browsing when GOOGLE_SAFE_BROWSING_KEY is set.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urljoin, urlparse

import httpx

from app.config import settings

URL_RE = re.compile(r"(?i)\b((?:https?://|www\.)[^\s<>\"']+|(?:[a-z0-9-]+\.)+(?:ly|gl|id|com|xyz|top|site|online|click|link|info|shop|app|me|co)/[^\s<>\"']*)")

SHORTENERS = {
    "bit.ly", "s.id", "tinyurl.com", "t.co", "goo.gl", "cutt.ly", "shorturl.at", "rb.gy", "is.gd",
    "ow.ly", "tiny.cc", "linktr.ee", "wa.me", "t.ly", "bit.do", "lnk.bio", "heylink.me",
}
RISKY_TLDS = {"xyz", "top", "site", "online", "click", "link", "info", "shop", "icu", "buzz", "monster", "rest", "cfd", "sbs", "lol"}
# Brand keyword -> official registrable domains
BRANDS = {
    "bca": {"bca.co.id", "klikbca.com"},
    "bri": {"bri.co.id"},
    "brimo": {"bri.co.id"},
    "mandiri": {"bankmandiri.co.id", "livin.id"},
    "bni": {"bni.co.id"},
    "btn": {"btn.co.id"},
    "bsi": {"bankbsi.co.id"},
    "jnt": {"jet.co.id"},
    "jne": {"jne.co.id"},
    "sicepat": {"sicepat.com"},
    "posindonesia": {"posindonesia.co.id"},
    "dana": {"dana.id"},
    "ovo": {"ovo.id"},
    "gopay": {"gojek.com", "gopay.co.id"},
    "shopee": {"shopee.co.id"},
    "tokopedia": {"tokopedia.com"},
    "pln": {"pln.co.id"},
    "bpjs": {"bpjs-kesehatan.go.id", "bpjsketenagakerjaan.go.id"},
    "ojk": {"ojk.go.id"},
    "iasc": {"ojk.go.id"},  # fake IASC sites exist; the real one is iasc.ojk.go.id
    "antam": {"antam.com", "logammulia.com"},
    "logammulia": {"logammulia.com"},
    "polri": {"polri.go.id"},
    "pajak": {"pajak.go.id"},
    "kemensos": {"kemensos.go.id"},
}

MAX_HOPS = 5


def extract_urls(text: str) -> list[str]:
    seen: list[str] = []
    for m in URL_RE.findall(text or ""):
        url = m.rstrip(".,);!?")
        if not url.lower().startswith("http"):
            url = "http://" + url
        if url not in seen:
            seen.append(url)
    return seen[:5]


def _registrable(host: str) -> str:
    parts = host.lower().strip(".").split(".")
    if len(parts) >= 3 and parts[-2] in {"co", "go", "or", "ac", "web", "my", "sch", "com", "net"} and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def heuristic_flags(url: str) -> list[str]:
    flags: list[str] = []
    p = urlparse(url)
    host = (p.hostname or "").lower()
    reg = _registrable(host)
    if p.scheme == "http":
        flags.append("tidak_https")
    try:
        ipaddress.ip_address(host)
        flags.append("alamat_ip_langsung")
    except ValueError:
        pass
    if host in SHORTENERS or reg in SHORTENERS:
        flags.append("link_pendek")
    if host.startswith("xn--") or ".xn--" in host:
        flags.append("domain_punycode")
    if host.rsplit(".", 1)[-1] in RISKY_TLDS:
        flags.append("domain_murahan")
    if p.path.lower().endswith(".apk"):
        flags.append("unduh_apk")
    squashed = re.sub(r"[^a-z0-9]", "", host)
    for brand, official in BRANDS.items():
        if brand in squashed and reg not in official:
            flags.append(f"meniru_{brand}")
            break
    if host.count("-") >= 3 or len(host) > 40:
        flags.append("domain_aneh")
    return flags


def _is_public_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


def expand_url(url: str) -> tuple[str, list[str], str | None]:
    """Follow redirects safely. Returns (final_url, chain, error)."""
    chain = [url]
    current = url
    with httpx.Client(timeout=5, follow_redirects=False, headers={"User-Agent": "Mozilla/5.0 CekDulu/1.0"}) as client:
        for _ in range(MAX_HOPS):
            host = urlparse(current).hostname or ""
            if not _is_public_host(host):
                return current, chain, "host_tidak_bisa_diakses"
            try:
                r = client.head(current)
                if r.status_code in (405, 403, 501):
                    r = client.get(current)
            except httpx.HTTPError as exc:
                return current, chain, f"gagal_dibuka:{type(exc).__name__}"
            loc = r.headers.get("location")
            if r.is_redirect and loc:
                current = urljoin(current, loc)
                chain.append(current)
                continue
            return current, chain, None
    return current, chain, "terlalu_banyak_redirect"


def _safe_browsing(urls: list[str]) -> dict[str, list[str]]:
    if not settings.safe_browsing_key or not urls:
        return {}
    body = {
        "client": {"clientId": "cek-dulu", "clientVersion": "1.0"},
        "threatInfo": {
            "threatTypes": ["MALWARE", "SOCIAL_ENGINEERING", "UNWANTED_SOFTWARE", "POTENTIALLY_HARMFUL_APPLICATION"],
            "platformTypes": ["ANY_PLATFORM"],
            "threatEntryTypes": ["URL"],
            "threatEntries": [{"url": u} for u in urls],
        },
    }
    try:
        r = httpx.post(
            "https://safebrowsing.googleapis.com/v4/threatMatches:find",
            params={"key": settings.safe_browsing_key},
            json=body,
            timeout=5,
        )
        r.raise_for_status()
    except httpx.HTTPError:
        return {}
    hits: dict[str, list[str]] = {}
    for m in r.json().get("matches", []):
        hits.setdefault(m["threat"]["url"], []).append(f"google_safe_browsing:{m['threatType'].lower()}")
    return hits


def check_link(url: str, expand: bool = True) -> dict:
    if not url.lower().startswith("http"):
        url = "http://" + url
    final_url, chain, error = expand_url(url) if expand else (url, [url], None)
    flags = heuristic_flags(url)
    if final_url != url:
        flags += [f for f in heuristic_flags(final_url) if f not in flags]
    sb = _safe_browsing(list({url, final_url}))
    for hits in sb.values():
        flags += hits
    return {
        "url": url,
        "final_url": final_url,
        "domain": urlparse(final_url).hostname,
        "redirects": len(chain) - 1,
        "flags": flags,
        "error": error,
        "reputation_source": "heuristics+safe_browsing" if settings.safe_browsing_key else "heuristics",
    }
