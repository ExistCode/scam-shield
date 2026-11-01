"""Offline tests run by default. Live LLM matrix: RUN_LIVE=1 META_API_KEY=... pytest -k live -s"""
import hashlib
import hmac
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
import yaml
from fastapi.testclient import TestClient

from app.agent import orchestrator
from app.agent.schema import Input, Verdict
from app.tools import links, qris

CASES = yaml.safe_load((Path(__file__).parent / "cases.yaml").read_text())


# --------------------------------------------------------------------------- QRIS

def tlv(tag: str, value: str) -> str:
    return f"{tag}{len(value):02d}{value}"


def make_qris(name="WARUNG BU SRI", city="BANDUNG", tamper_crc=False) -> str:
    mai = tlv("00", "ID.CO.QRIS.WWW") + tlv("02", "ID1020021234567") + tlv("03", "UMI")
    body = (tlv("00", "01") + tlv("01", "11") + tlv("51", mai) + tlv("52", "5812") + tlv("53", "360")
            + tlv("58", "ID") + tlv("59", name) + tlv("60", city) + "6304")
    crc = qris.crc16_ccitt(body)
    if tamper_crc:
        crc = "0000" if crc != "0000" else "FFFF"
    return body + crc


def qr_png(text: str) -> bytes:
    img = cv2.QRCodeEncoder.create().encode(text)
    img = cv2.resize(img, None, fx=8, fy=8, interpolation=cv2.INTER_NEAREST)
    img = cv2.copyMakeBorder(img, 40, 40, 40, 40, cv2.BORDER_CONSTANT, value=255)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


def test_crc16_known_vector():
    assert qris.crc16_ccitt("123456789") == "29B1"  # CRC-16/CCITT-FALSE check value


def test_parse_valid_qris():
    q = qris.parse_qris(make_qris())
    assert q["merchant_name"] == "WARUNG BU SRI"
    assert q["merchant_city"] == "BANDUNG"
    assert q["nmid"] == "ID1020021234567"
    assert q["crc_valid"] is True
    assert q["dynamic"] is False
    assert q["flags"] == []


def test_parse_tampered_qris():
    q = qris.parse_qris(make_qris(tamper_crc=True))
    assert q["crc_valid"] is False
    assert "crc_tidak_valid" in q["flags"]


def test_parse_garbage():
    q = qris.parse_qris("https://example.com")
    assert q["is_emv"] is False and q["crc_valid"] is False


def test_decode_qris_from_image():
    q = qris.decode_qris(qr_png(make_qris(name="TOKO MAJU JAYA")))
    assert q is not None and q["merchant_name"] == "TOKO MAJU JAYA" and q["crc_valid"]


@pytest.mark.parametrize("name", ["qris_palsu_tempelan", "qris_crc_rusak", "qris_asli", "qris_kotak_infaq"])
def test_decode_generated_stickers(name):
    path = Path(__file__).parent / "manual" / "assets" / f"{name}.png"
    if not path.exists():
        pytest.skip("run: python -m tests.manual.make_assets")
    q = qris.decode_qris(path.read_bytes())
    assert q is not None and q["merchant_name"], name


def test_decode_no_qr():
    blank = cv2.imencode(".png", np.full((200, 200), 255, np.uint8))[1].tobytes()
    assert qris.decode_qris(blank) is None


# --------------------------------------------------------------------------- links

def test_extract_urls():
    urls = links.extract_urls("cek di bit.ly/abc123 atau https://bri-tarif.site/form, makasih.")
    assert "http://bit.ly/abc123" in urls and "https://bri-tarif.site/form" in urls


@pytest.mark.parametrize(
    "url,flag",
    [
        ("http://bit.ly/x", "link_pendek"),
        ("https://bca-verifikasi.xyz/login", "meniru_bca"),
        ("https://klikbca.com", None),
        ("http://sicepat-resi.xyz/resi.apk", "unduh_apk"),
        ("http://192.168.1.1/a", "alamat_ip_langsung"),
    ],
)
def test_heuristics(url, flag):
    flags = links.heuristic_flags(url)
    if flag:
        assert flag in flags
    else:
        assert not any(f.startswith("meniru_") for f in flags)


def test_ssrf_guard_blocks_localhost():
    final, _chain, err = links.expand_url("http://127.0.0.1:9/")
    assert err == "host_tidak_bisa_diakses"


# --------------------------------------------------------------------------- orchestrator (fake model)

def _resp(content=None, tool_calls=None):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls,
                          model_dump=lambda **_: {"role": "assistant", "content": content,
                                                  "tool_calls": [{"id": t.id, "type": "function",
                                                                  "function": {"name": t.function.name, "arguments": t.function.arguments}}
                                                                 for t in (tool_calls or [])]})
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def _verdict_json(**kw):
    base = dict(risk="BAHAYA", category="cs_bank_palsu", confidence=0.9, reason="Minta OTP.",
                next_steps=["Jangan kirim OTP."], alert_guardian=True, reply_text="Ini penipuan, Bu.")
    base.update(kw)
    return json.dumps(base)


class FakeProvider:
    name, model = "fake", "fake-1"
    supports_images = False

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def chat(self, messages, tools=None, response_format=None):
        self.calls.append({"messages": list(messages), "tools": tools, "response_format": response_format})
        return self.script.pop(0)


@pytest.fixture
def fake(monkeypatch):
    def install(script):
        p = FakeProvider(script)
        monkeypatch.setattr(orchestrator, "get_provider", lambda name=None: p)
        monkeypatch.setattr(orchestrator, "fallback_name", lambda primary: None)
        return p
    return install


def test_direct_verdict(fake):
    p = fake([_resp(_verdict_json())])
    r = orchestrator.analyze(Input(type="text", text="CS BCA, kirim OTP"))
    assert r.verdict.risk == "BAHAYA" and r.verdict.alert_guardian
    assert "<pesan_diteruskan>" in p.calls[0]["messages"][1]["content"]


def test_tool_loop(fake, monkeypatch):
    monkeypatch.setattr(orchestrator.links, "check_link", lambda url: {"url": url, "flags": ["link_pendek"]})
    tc = SimpleNamespace(id="t1", function=SimpleNamespace(name="check_link", arguments='{"url": "http://bit.ly/x"}'))
    fake([_resp(tool_calls=[tc]), _resp("```json\n" + _verdict_json() + "\n```")])
    r = orchestrator.analyze(Input(type="text", text="klik bit.ly/x"))
    assert "check_link" in r.tools_used
    assert r.verdict.risk == "BAHAYA"


def test_invalid_json_repaired(fake):
    p = fake([_resp("hmm, ini kayaknya penipuan"), _resp(_verdict_json(risk="WASPADA", category="lainnya"))])
    r = orchestrator.analyze(Input(type="text", text="halo"))
    assert r.verdict.risk == "WASPADA"
    assert p.calls[1]["response_format"]["type"] == "json_schema"


def test_invalid_twice_falls_back(fake):
    fake([_resp("nope"), _resp("still nope")])
    r = orchestrator.analyze(Input(type="text", text="halo"))
    assert r.verdict.risk == "WASPADA" and r.verdict.category == "lainnya"


def test_safety_low_confidence_aman_becomes_waspada(fake):
    fake([_resp(_verdict_json(risk="AMAN", category="lainnya", confidence=0.4, alert_guardian=False,
                              reply_text="Pesan ini 100% aman."))])
    r = orchestrator.analyze(Input(type="text", text="arisan"))
    assert r.verdict.risk == "WASPADA"
    assert "100%" not in r.verdict.reply_text


def test_safety_apk_forces_bahaya(fake):
    fake([_resp(_verdict_json(risk="WASPADA", category="lainnya", alert_guardian=False))])
    r = orchestrator.analyze(Input(type="document", text="[File terlampir: resi.apk]"))
    assert r.verdict.risk == "BAHAYA" and r.verdict.category == "kurir_apk" and r.verdict.alert_guardian


def test_qris_image_context(fake, monkeypatch):
    monkeypatch.setattr(orchestrator, "settings", SimpleNamespace(meta_api_key="", max_tool_steps=4, llm_provider="meta"))
    p = fake([_resp(_verdict_json(risk="WASPADA", category="qris_palsu", alert_guardian=False))])
    r = orchestrator.analyze(Input(type="image", text="Toko Maju Jaya", media_bytes=qr_png(make_qris("ANDI CELL"))))
    assert "decode_qris" in r.tools_used
    assert "ANDI CELL" in p.calls[0]["messages"][1]["content"]


def test_image_single_call(fake):
    p = fake([_resp(_verdict_json(risk="WASPADA", category="qris_palsu", alert_guardian=False))])
    p.supports_images = True
    r = orchestrator.analyze(Input(type="image", text="Warung Bu Sri", media_bytes=qr_png(make_qris("ANDI CELL")), mime="image/png"))
    content = p.calls[0]["messages"][1]["content"]
    assert isinstance(content, list) and content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "ANDI CELL" in content[0]["text"]
    assert len(p.calls) == 1 and r.image_mode == "single_call" and "muse_vision" in r.tools_used


def test_image_falls_back_to_two_step_when_rejected(fake, monkeypatch):
    import httpx
    from openai import BadRequestError
    req = httpx.Request("POST", "http://x")
    bad = BadRequestError("no images", response=httpx.Response(400, request=req), body=None)
    calls = {"n": 0}

    class P(FakeProvider):
        supports_images = True

        def chat(self, messages, tools=None, response_format=None):
            calls["n"] += 1
            if isinstance(messages[1]["content"], list):
                raise bad
            return _resp(_verdict_json(risk="WASPADA", category="lainnya", alert_guardian=False))

    prov = P([])
    monkeypatch.setattr(orchestrator, "get_provider", lambda name=None: prov)
    monkeypatch.setattr(orchestrator, "fallback_name", lambda primary: None)
    monkeypatch.setattr(orchestrator.vision, "describe_image",
                        lambda b, m=None: {"kind": "chat", "extracted_text": "transfer ke rek 123", "notes": "chat"})
    monkeypatch.setattr(orchestrator, "settings", SimpleNamespace(meta_api_key="k", max_tool_steps=4, llm_provider="meta"))
    r = orchestrator.analyze(Input(type="image", media_bytes=b"not-a-real-image", mime="image/jpeg"))
    assert r.image_mode == "two_step" and "describe_image" in r.tools_used and r.verdict.risk == "WASPADA"


def test_overrides_recorded(fake):
    fake([_resp(_verdict_json(risk="WASPADA", category="lainnya", alert_guardian=False))])
    r = orchestrator.analyze(Input(type="document", text="[File terlampir: resi.apk]"))
    assert r.model_risk == "WASPADA" and r.verdict.risk == "BAHAYA"
    assert {"rule": "apk_selalu_bahaya", "field": "risk", "from": "WASPADA", "to": "BAHAYA"} in r.overrides
    d = orchestrator.details(r)
    assert d["model_risk"] == "WASPADA" and d["overrides"]


@pytest.mark.parametrize("text,expected", [
    # multi-sentence: drop only the softening sentence
    ("Ibu, ini penipuan. Tidak usah khawatir ya. Hapus saja pesannya.", "Ibu, ini penipuan. Hapus saja pesannya."),
    ("Ini tipuan ya Bu. Jangan khawatir, Ibu sudah benar.", "Ini tipuan ya Bu."),
    # single sentence: cut the clause
    ("Ibu/Bapak, terima kasih sudah tanya dulu, pesan ini tipuan jadi tidak usah khawatir ya.",
     "Ibu/Bapak, terima kasih sudah tanya dulu, pesan ini tipuan."),
    # nothing to remove
    ("Ibu, ini penipuan, jangan panik dan jangan diikuti ya.", "Ibu, ini penipuan, jangan panik dan jangan diikuti ya."),
])
def test_drop_softeners(text, expected):
    assert orchestrator._drop_softeners(text, "BAHAYA") == expected


def test_softener_removed_on_bahaya_but_kept_on_aman(fake):
    fake([_resp(_verdict_json(reply_text="Ini penipuan, Bu. Jangan khawatir ya."))])
    r = orchestrator.analyze(Input(type="text", text="kirim OTP"))
    assert r.verdict.reply_text == "Ini penipuan, Bu." and any(o["rule"] == "hapus_jangan_khawatir" for o in r.overrides)
    fake([_resp(_verdict_json(risk="AMAN", category="lainnya", confidence=0.9, alert_guardian=False,
                              reply_text="Ini kabar biasa, tidak usah khawatir ya."))])
    r = orchestrator.analyze(Input(type="text", text="arisan"))
    assert "khawatir" in r.verdict.reply_text


def test_raised_risk_replaces_reply_text(fake):
    fake([_resp(_verdict_json(risk="AMAN", category="lainnya", confidence=0.4, alert_guardian=False,
                              reply_text="Pesan ini aman kok Bu, santai saja."))])
    r = orchestrator.analyze(Input(type="text", text="halo"))
    assert r.verdict.risk == "WASPADA" and "santai" not in r.verdict.reply_text
    assert r.verdict.reply_text == orchestrator._DEFAULT_REPLY["WASPADA"]


# --------------------------------------------------------------------------- webhook

@pytest.fixture
def client():
    from app.main import app
    with TestClient(app) as c:
        yield c


def _sig(body: bytes) -> str:
    return "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()


def test_webhook_verify(client):
    r = client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "test-verify", "hub.challenge": "42"})
    assert r.status_code == 200 and r.text == "42"
    assert client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "x"}).status_code == 403


def test_webhook_rejects_bad_signature(client):
    assert client.post("/webhook", content=b"{}", headers={"X-Hub-Signature-256": "sha256=00"}).status_code == 401


def _wa_payload(mid, text, sender="6281111111111"):
    return {"entry": [{"changes": [{"value": {
        "contacts": [{"wa_id": sender, "profile": {"name": "Sri"}}],
        "messages": [{"id": mid, "from": sender, "type": "text", "text": {"body": text}}]}}]}]}


def test_webhook_end_to_end_with_guardian(client, monkeypatch):
    from app.channels import whatsapp_sender, whatsapp_webhook
    sent, alerts = [], []
    monkeypatch.setattr(whatsapp_sender, "send_text", lambda to, body, reply_to=None: sent.append((to, body)))
    monkeypatch.setattr(whatsapp_sender, "send_guardian_alert", lambda g, label, v: alerts.append((g, label, v.risk)))
    monkeypatch.setattr(whatsapp_webhook, "analyze", lambda inp: orchestrator.AnalysisResult(
        verdict=Verdict.model_validate_json(_verdict_json()), provider="fake", model="fake"))

    def post(payload):
        body = json.dumps(payload).encode()
        return client.post("/webhook", content=body, headers={"X-Hub-Signature-256": _sig(body)})

    assert post(_wa_payload("m1", "daftar wali 0812 2222 3333")).status_code == 200
    assert any("Wali terdaftar: +6281222223333" in b for _, b in sent)

    assert post(_wa_payload("m2", "CS BCA: kirim OTP sekarang")).status_code == 200
    assert any("*BAHAYA*" in b for _, b in sent)
    assert alerts and alerts[0][0] == "6281222223333"

    # duplicate delivery is ignored
    n = len(sent)
    post(_wa_payload("m2", "CS BCA: kirim OTP sekarang"))
    assert len(sent) == n

    # cooldown: same category again within 10 min -> no second alert
    post(_wa_payload("m3", "CS BCA lagi: kirim OTP"))
    assert len(alerts) == 1


def test_normalize_number():
    from app.db.repo import normalize_number
    assert normalize_number("0812-3456-7890") == "6281234567890"
    assert normalize_number("+62 812 3456 7890") == "6281234567890"
    assert normalize_number("123") is None


# --------------------------------------------------------------------------- live matrix

live = pytest.mark.skipif(not (os.environ.get("RUN_LIVE") and os.environ.get("META_API_KEY")),
                          reason="set RUN_LIVE=1 and META_API_KEY to run against Muse Spark")


@live
@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_live_matrix(case):
    r = orchestrator.analyze(Input(type=case["type"], text=case["text"]))
    allowed = {case["expected"], *case.get("accept", [])}
    if r.verdict.risk in ("WASPADA", "BAHAYA"):
        assert not orchestrator._SOFTENER.search(r.verdict.reply_text), r.verdict.reply_text
        assert "pintar" not in r.verdict.reply_text.lower(), r.verdict.reply_text
    said = " ".join([r.verdict.reply_text, r.verdict.reason, *r.verdict.next_steps]).lower()
    print(f"\n{case['id']}: {r.verdict.risk}/{r.verdict.category} conf={r.verdict.confidence:.2f} "
          f"tools={r.tools_used} {r.latency_ms}ms overrides={[o['rule'] for o in r.overrides]}\n  {r.verdict.reply_text}"
          f"\n  steps: {r.verdict.next_steps}")
    assert r.verdict.risk in allowed
    for pattern in case.get("must_mention", []):
        assert re.search(pattern, said), f"expected /{pattern}/ in: {said}"


ASSETS = Path(__file__).parent / "manual" / "assets"
IMAGE_CASES = [
    ("qris_palsu_tempelan.png", "Mau bayar di Warung Bu Sri, aman ga?", {"WASPADA", "BAHAYA"}),
    ("resi_palsu_chat.png", "", {"BAHAYA"}),
    ("qris_kotak_infaq.png", "Ini QRIS di kotak infaq masjid, mau sedekah", {"WASPADA", "BAHAYA"}),
    ("toko_online_palsu_chat.png", "Bu ini promo kulkas murah, beli ga ya", {"BAHAYA", "WASPADA"}),
]
IMAGE_MUST_MENTION = {"toko_online_palsu_chat.png": "cekrekening", "qris_kotak_infaq.png": "toko pulsa jaya|nama"}


@live
@pytest.mark.parametrize("fname,caption,allowed", IMAGE_CASES, ids=[c[0] for c in IMAGE_CASES])
def test_live_images(fname, caption, allowed):
    path = ASSETS / fname
    if not path.exists():
        pytest.skip("run: python -m tests.manual.make_assets")
    r = orchestrator.analyze(Input(type="image", text=caption, media_bytes=path.read_bytes(), mime="image/png"))
    said = " ".join([r.verdict.reply_text, r.verdict.reason, *r.verdict.next_steps]).lower()
    print(f"\n{fname}: {r.verdict.risk}/{r.verdict.category} muse={r.model_risk} mode={r.image_mode} "
          f"tools={r.tools_used} {r.latency_ms}ms\n  {r.verdict.reason}\n  steps: {r.verdict.next_steps}")
    assert r.verdict.risk in allowed
    if fname in IMAGE_MUST_MENTION:
        assert re.search(IMAGE_MUST_MENTION[fname], said), said


VOICE_CASES = [
    ("voice_kecelakaan.ogg", {"WASPADA", "BAHAYA"}, "kecelakaan"),
    ("voice_polisi_gadungan.ogg", {"BAHAYA"}, "polda"),
    ("video_polisi_gadungan.mp4", {"BAHAYA"}, "polda"),
]


@live
@pytest.mark.parametrize("fname,allowed,must_hear", VOICE_CASES, ids=[c[0] for c in VOICE_CASES])
def test_live_voice(fname, allowed, must_hear):
    path = ASSETS / fname
    if not path.exists():
        pytest.skip("run: python -m tests.manual.make_assets")
    is_video = fname.endswith(".mp4")
    r = orchestrator.analyze(Input(type="video" if is_video else "audio", media_bytes=path.read_bytes(),
                                   mime="video/mp4" if is_video else "audio/ogg"))
    t = r.tool_outputs.get("transcribe_audio", {}).get("transcript", "")
    print(f"\n{fname}: {r.verdict.risk}/{r.verdict.category} tools={r.tools_used} {r.latency_ms}ms\n"
          f"  transcript: {t}\n  {r.verdict.reply_text}")
    assert "transcribe_audio" in r.tools_used and must_hear in t.lower()
    assert r.verdict.risk in allowed


# --------------------------------------------------------------------------- identifiers (account / phone)

@pytest.mark.parametrize("text,accounts,phones", [
    ("Transfer DP ke rek admin, BRI 0123-4567-89 a.n. Siska. Info WA 0857-1122-3344", ["0123456789"], ["085711223344"]),
    ("DANA 081234567890 a.n. Budi", ["081234567890"], []),
    ("rekening BCA 1234567890", ["1234567890"], []),
    ("Hubungi admin di WA +62 857-1122-3344", [], ["085711223344"]),
    ("arisan jam 4 di rumah no 12", [], []),
])
def test_identifiers(text, accounts, phones):
    from app.tools import identifiers
    got_acc = identifiers.extract_accounts(text)
    assert got_acc == accounts
    assert [p for p in identifiers.extract_phones(text) if p not in got_acc] == phones


def test_identifier_hints_reach_the_model(fake):
    p = fake([_resp(_verdict_json(category="belanja_online"))])
    orchestrator.analyze(Input(type="text", text="DP ke rek pribadi BCA 1234567890 a.n. Siska, WA 081299887766"))
    msg = p.calls[0]["messages"][1]["content"]
    assert "cekrekening.id" in msg and "aduannomor.id" in msg


def test_video_audio_extraction():
    path = Path(__file__).parent / "manual" / "assets" / "video_polisi_gadungan.mp4"
    if not path.exists():
        pytest.skip("run: python -m tests.manual.make_assets")
    from app.tools.transcribe import to_wav
    wav = to_wav(path.read_bytes())
    assert wav[:4] == b"RIFF" and len(wav) > 100_000
