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
from app.agent.schema import FALLBACK_VERDICT, AnalysisResult, Input, Verdict
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
    assert "Ada kode QR" in p.calls[0]["messages"][1]["content"]
    assert "decode_qris" in [t["function"]["name"] for t in p.calls[0]["tools"]]
    # the model didn't call it: the QR checks still run, and that's recorded
    assert "decode_qris" in r.tools_used and r.tool_outputs["decode_qris"]["merchant_name"] == "ANDI CELL"
    assert any(o["rule"] == "qris_dibaca_otomatis" for o in r.overrides)


def test_model_calls_decode_qris(fake, monkeypatch):
    monkeypatch.setattr(orchestrator, "settings", SimpleNamespace(meta_api_key="", max_tool_steps=4, llm_provider="meta"))
    call = SimpleNamespace(id="c1", function=SimpleNamespace(name="decode_qris", arguments="{}"))
    p = fake([_resp(tool_calls=[call]), _resp(_verdict_json(risk="BAHAYA", category="qris_palsu"))])
    r = orchestrator.analyze(Input(type="image", text="Toko Maju Jaya", media_bytes=qr_png(make_qris("ANDI CELL"))))
    tool_msg = p.calls[1]["messages"][-1]
    assert tool_msg["role"] == "tool" and "ANDI CELL" in tool_msg["content"] and '"found": true' in tool_msg["content"]
    assert r.tool_outputs["decode_qris"]["merchant_name"] == "ANDI CELL"
    assert not any(o["rule"] == "qris_dibaca_otomatis" for o in r.overrides)


def test_text_input_has_no_qris_tool(fake):
    p = fake([_resp(_verdict_json())])
    orchestrator.analyze(Input(type="text", text="CS BCA, kirim OTP"))
    assert "decode_qris" not in [t["function"]["name"] for t in p.calls[0]["tools"]]


def test_apk_gets_installed_step(fake):
    fake([_resp(_verdict_json(risk="BAHAYA", category="kurir_apk", next_steps=["Jangan buka.", "Hapus pesannya."]))])
    r = orchestrator.analyze(Input(type="document", text="[File terlampir: resi.apk]"))
    assert len(r.verdict.next_steps) == 3 and "terlanjur dipasang" in r.verdict.next_steps[-1]
    fake([_resp(_verdict_json(risk="BAHAYA", category="kurir_apk",
                              next_steps=["Jangan buka.", "Kalau sudah terpasang, matikan internet."]))])
    r = orchestrator.analyze(Input(type="document", text="[File terlampir: resi.apk]"))
    assert len(r.verdict.next_steps) == 2  # model already said it


def test_image_single_call(fake):
    p = fake([_resp(_verdict_json(risk="WASPADA", category="qris_palsu", alert_guardian=False))])
    p.supports_images = True
    r = orchestrator.analyze(Input(type="image", text="Warung Bu Sri", media_bytes=qr_png(make_qris("ANDI CELL")), mime="image/png"))
    content = p.calls[0]["messages"][1]["content"]
    assert isinstance(content, list) and content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "Ada kode QR" in content[0]["text"]
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
    assert any("+6281****333 (keluarga) sekarang ikut jagain" in b for _, b in sent)

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


_OWNER_CLAIM = re.compile(r"(?i)(pemilik nomor|nomor (ini|itu|tersebut) (milik|terdaftar|berasal|dari daerah)|"
                          r"\b(telkomsel|indosat|smartfren)\b)")


@live
@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_live_matrix(case):
    r = orchestrator.analyze(Input(type=case["type"], text=case["text"]))
    allowed = {case["expected"], *case.get("accept", [])}
    if r.verdict.risk in ("WASPADA", "BAHAYA"):
        assert not orchestrator._SOFTENER.search(r.verdict.reply_text), r.verdict.reply_text
        assert "pintar" not in r.verdict.reply_text.lower(), r.verdict.reply_text
    out = format_reply(r.verdict)
    assert contacts.unofficial_numbers(out, r.verdict.scam_phone_numbers + r.verdict.scam_account_numbers) == [], out
    assert not _OWNER_CLAIM.search(out), out
    said = out.lower()
    print(f"\n{case['id']}: {r.verdict.risk}/{r.verdict.category} conf={r.verdict.confidence:.2f} "
          f"tools={r.tools_used} {r.latency_ms}ms overrides={[o['rule'] for o in r.overrides]}\n  {r.verdict.reply_text}"
          f"\n  steps: {r.verdict.next_steps}")
    assert r.verdict.risk in allowed
    for pattern in case.get("must_mention", []):
        assert re.search(pattern, said), f"expected /{pattern}/ in: {said}"


ASSETS = Path(__file__).parent / "manual" / "assets"
IMAGE_CASES = [
    ("qris_palsu_tempelan.png", "Mau bayar di Warung Bu Sri, aman ga?", {"WASPADA", "BAHAYA"}),
    ("qris_warung_foto.jpg", "Mau bayar di Warung Bu Sri pakai QRIS ini, aman ga?", {"BAHAYA"}),
    ("resi_palsu_chat.png", "", {"BAHAYA"}),
    ("qris_kotak_infaq.png", "Ini QRIS di kotak infaq masjid, mau sedekah", {"WASPADA", "BAHAYA"}),
    ("toko_online_palsu_chat.png", "Bu ini promo kulkas murah, beli ga ya", {"BAHAYA", "WASPADA"}),
]
IMAGE_MUST_MENTION = {"toko_online_palsu_chat.png": "transfer",
                      "resi_palsu_chat.png": r"laporkan nomor 0857-2231-0098 di aduannomor\.id", "qris_kotak_infaq.png": "toko pulsa jaya|nama"}


@live
@pytest.mark.parametrize("fname,caption,allowed", IMAGE_CASES, ids=[c[0] for c in IMAGE_CASES])
def test_live_images(fname, caption, allowed):
    path = ASSETS / fname
    if not path.exists():
        pytest.skip("run: python -m tests.manual.make_assets")
    mime = "image/jpeg" if fname.endswith(".jpg") else "image/png"
    r = orchestrator.analyze(Input(type="image", text=caption, media_bytes=path.read_bytes(), mime=mime))
    if fname == "qris_warung_foto.jpg":  # too small/blurry for the local scan: SAM 3.1 must find it
        assert "sam_crop" in r.tools_used and r.tool_outputs["decode_qris"]["merchant_name"] == "ANDI CELL"
    out = format_reply(r.verdict)
    assert contacts.unofficial_numbers(out, r.verdict.scam_phone_numbers + r.verdict.scam_account_numbers) == [], out
    assert not _OWNER_CLAIM.search(out), out
    said = out.lower()
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
    assert "1234567890" in msg and "081299887766" in msg  # the model is told what was spotted


# --------------------------------------------------------------------------- official contacts

from app.agent import contacts  # noqa: E402
from app.channels.formatting import format_reply  # noqa: E402


def test_fake_police_gets_110(fake):
    fake([_resp(_verdict_json(category="aparat_palsu", scam_type="fake_police",
                              next_steps=["Tutup telepon.", "Jangan transfer."]))])
    r = orchestrator.analyze(Input(type="text", text="Kami dari Polda, transfer ke rekening PPATK untuk pembuktian"))
    out = format_reply(r.verdict)
    assert "*110*" in out and "iasc" not in out.lower()  # no money sent yet: no IASC


def test_scam_type_falls_back_to_category(fake):
    fake([_resp(_verdict_json(category="aparat_palsu", scam_type="other"))])
    r = orchestrator.analyze(Input(type="text", text="Kami dari Polda Metro Jaya"))
    assert r.verdict.scam_type == "fake_police" and "*110*" in format_reply(r.verdict)


def test_money_sent_gets_bank_and_iasc_first(fake):
    fake([_resp(_verdict_json(category="cs_bank_palsu", scam_type="bank_otp", money_sent=True))])
    r = orchestrator.analyze(Input(type="text", text="Aduh sudah saya transfer 2 juta ke CS BCA tadi"))
    out = format_reply(r.verdict)
    assert "1500888" in out and "https://iasc.ojk.go.id" in out
    assert out.index("1500888") < out.index("iasc.ojk.go.id")  # bank first, then IASC
    assert "12 jam" in out


def test_investment_and_pinjol_get_ojk(fake):
    for cat, st in (("investasi", "investment"), ("pinjol", "pinjol")):
        fake([_resp(_verdict_json(category=cat, scam_type=st))])
        out = format_reply(orchestrator.analyze(Input(type="text", text="profit 3% per hari")).verdict)
        assert "157" in out and "081-157-157-157" in out


REPORT = "Laporkan nomor {} di aduannomor.id (situs resmi Komdigi)."


@pytest.mark.parametrize("risk", ["BAHAYA", "WASPADA"])
def test_scam_number_in_text_gets_report_step(fake, risk):
    fake([_resp(_verdict_json(risk=risk, category="hadiah_palsu", scam_type="other",
                              next_steps=["Jangan bayar apa pun.", "Abaikan pesannya.", "Blokir nomornya."]))])
    r = orchestrator.analyze(Input(type="text", text="Menang undian! Hubungi admin WA +62 857-1122-3344"))
    assert r.verdict.next_steps[-1] == REPORT.format("0857-1122-3344")  # +62 normalized to 08xx
    assert len(r.verdict.next_steps) == 4  # the model's 3 steps are kept
    out = format_reply(r.verdict)
    assert "4. Laporkan nomor 0857-1122-3344 di aduannomor.id (situs resmi Komdigi)." in out
    assert contacts.unofficial_numbers(out, r.verdict.scam_phone_numbers) == []


def test_aman_never_gets_report_step(fake):
    fake([_resp(_verdict_json(risk="AMAN", category="lainnya", confidence=0.9, alert_guardian=False,
                              reply_text="Aman kok.", scam_phone_numbers=["081234567890"]))])
    r = orchestrator.analyze(Input(type="text", text="Saya driver GoFood, nomor saya 0812-3456-7890, pesanan di depan"))
    assert r.verdict.scam_phone_numbers == [] and "aduannomor" not in format_reply(r.verdict)


def test_model_number_must_appear_in_text(fake):
    # A number the model "saw" that isn't in the forwarded text is not reported (no hallucinated numbers).
    fake([_resp(_verdict_json(scam_phone_numbers=["0899-9999-0000", "0857 1122 3344"]))])
    r = orchestrator.analyze(Input(type="text", text="CS BCA: kirim OTP ke WA 0857 1122 3344"))
    assert r.verdict.scam_phone_numbers == ["0857-1122-3344"]


def test_screenshot_number_comes_from_model(fake):
    p = fake([_resp(_verdict_json(category="kurir_apk", scam_type="other", scam_phone_numbers=["+62 857-2231-0098"]))])
    p.supports_images = True
    r = orchestrator.analyze(Input(type="image", media_bytes=b"png", mime="image/png"))
    assert r.verdict.next_steps[-1] == REPORT.format("0857-2231-0098")


def test_voice_transcript_number(fake, monkeypatch):
    monkeypatch.setattr(orchestrator.transcribe, "transcribe_audio",
                        lambda b, m=None: {"transcript": "Ini dari Polda, hubungi penyidik di nomor 0812 7777 6666 sekarang"})
    fake([_resp(_verdict_json(category="aparat_palsu", scam_type="fake_police"))])
    r = orchestrator.analyze(Input(type="audio", media_bytes=b"ogg", mime="audio/ogg"))
    assert REPORT.format("0812-7777-6666") in r.verdict.next_steps


def test_report_step_never_claims_owner_or_location():
    step = contacts.report_step(["0857-1122-3344"])
    assert not re.search(r"(?i)milik|pemilik|atas nama|lokasi|daerah|kota|telkomsel|indosat|\bxl\b|smartfren", step)
    assert contacts.normalize_phone("6285711223344") == "0857-1122-3344"
    assert contacts.normalize_phone("1500888") is None and contacts.normalize_phone("0211234567") is None


def test_aman_has_no_contacts(fake):
    fake([_resp(_verdict_json(risk="AMAN", category="lainnya", confidence=0.9, alert_guardian=False,
                              reply_text="Ini aman, Bu."))])
    r = orchestrator.analyze(Input(type="text", text="Bu, posyandu besok jam 8"))
    assert r.verdict.contacts == [] and "Kontak resmi" not in format_reply(r.verdict)


@pytest.mark.parametrize("invented", [
    "Hubungi Mandiri di 14000 sekarang.",
    "Telepon CS di 0812-3456-7890 ya.",
    "Lapor ke WA +62 811 2222 3333.",
    "Transfer balik ke BRI 0123456789.",
    "Telepon hotline 1500017.",
])
def test_no_number_outside_contacts_ever_reaches_the_reply(fake, invented):
    fake([_resp(_verdict_json(category="cs_bank_palsu", scam_type="bank_otp", money_sent=True,
                              reply_text="Ini penipuan, Bu. " + invented, reason=invented,
                              next_steps=[invented, "Jangan kirim OTP."]))])
    r = orchestrator.analyze(Input(type="text", text="CS BCA minta OTP, sudah saya kirim"))
    out = format_reply(r.verdict)
    assert contacts.unofficial_numbers(out) == [], out
    assert "1500888" in out and "iasc.ojk.go.id" in out  # the official ones are still there
    assert any(o["rule"] == "nomor_tidak_resmi_dihapus" for o in r.overrides)


def test_contacts_map_only_has_verified_entries():
    assert contacts.ALLOWED_NUMBERS == {"110", "157", "081157157157", "1500888"}
    sites = {c["contact"] for c in contacts.ALL_CONTACTS if c["contact"].startswith("http")}
    assert sites == {"https://iasc.ojk.go.id", "https://aduannomor.id"}


def test_video_audio_extraction():
    path = Path(__file__).parent / "manual" / "assets" / "video_polisi_gadungan.mp4"
    if not path.exists():
        pytest.skip("run: python -m tests.manual.make_assets")
    from app.tools.transcribe import to_wav
    wav = to_wav(path.read_bytes())
    assert wav[:4] == b"RIFF" and len(wav) > 100_000


def test_analyze_endpoint_is_gated(monkeypatch):
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from app.api import analyze as api
    from app.main import app

    monkeypatch.setattr(api, "analyze", lambda inp: AnalysisResult(verdict=FALLBACK_VERDICT, provider="fake", model="fake"))
    api._hits.clear()
    c = TestClient(app)
    body = {"type": "text", "text": "halo"}

    monkeypatch.setattr(api, "settings", SimpleNamespace(analyze_token="", production=True, analyze_rate_per_min=10))
    assert c.post("/analyze", json=body).status_code == 403          # prod without a token: off

    monkeypatch.setattr(api, "settings", SimpleNamespace(analyze_token="demo", production=True, analyze_rate_per_min=3))
    assert c.post("/analyze", json=body).status_code == 401
    assert c.post("/analyze", json=body, headers={"X-Analyze-Token": "nope"}).status_code == 401
    ok = [c.post("/analyze", json=body, headers={"X-Analyze-Token": "demo"}).status_code for _ in range(4)]
    assert ok == [200, 200, 200, 429]                                 # rate limited per IP


# --------------------------------------------------------------------------- Muse Wonder escalation

def _named_provider(name, model, verdict_json, calls):
    class P:
        supports_images = True

        def chat(self, messages, tools=None, response_format=None):
            calls.append(name)
            msg = SimpleNamespace(content=verdict_json, tool_calls=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])
    p = P()
    p.name, p.model = name, model
    return p


@pytest.fixture
def spark_and_wonder(monkeypatch):
    def install(spark_json, wonder_json=None, wonder_exc=None):
        calls = []
        spark = _named_provider("meta", "muse-spark-1.3", spark_json, calls)
        wonder = _named_provider("wonder", "muse-wonder-1.0", wonder_json, calls)
        if wonder_exc:
            wonder.chat = lambda *a, **k: calls.append("wonder") or (_ for _ in ()).throw(wonder_exc)

        def get(name=None):
            return wonder if name == "wonder" else spark
        monkeypatch.setattr(orchestrator, "get_provider", get)
        monkeypatch.setattr(orchestrator, "fallback_name", lambda primary: None)
        return calls
    return install


def test_confident_spark_is_not_escalated(spark_and_wonder):
    calls = spark_and_wonder(_verdict_json(confidence=0.95))
    r = orchestrator.analyze(Input(type="text", text="CS BCA minta OTP"))
    assert calls == ["meta"] and r.escalation is None and r.provider == "meta"


def test_unsure_spark_escalates_to_wonder(spark_and_wonder):
    from app.channels.formatting import tools_footer
    calls = spark_and_wonder(_verdict_json(risk="WASPADA", category="lainnya", confidence=0.55, alert_guardian=False),
                             _verdict_json(risk="BAHAYA", category="lowongan_kerja", confidence=0.9))
    r = orchestrator.analyze(Input(type="text", text="Kerja part time like video, deposit dulu 200rb"))
    assert calls == ["meta", "wonder"]
    assert r.verdict.risk == "BAHAYA" and r.provider == "wonder" and r.model == "muse-wonder-1.0"
    assert r.escalation == {"reason": "low_confidence",
                            "spark": {"risk": "WASPADA", "category": "lainnya", "confidence": 0.55},
                            "wonder": {"risk": "BAHAYA", "category": "lowongan_kerja", "confidence": 0.9}}
    assert "Muse Spark → Muse Wonder (cek ulang)" in tools_footer(r)
    assert orchestrator.details(r)["escalation"]["spark"]["risk"] == "WASPADA"


def test_wonder_failure_keeps_spark_verdict(spark_and_wonder):
    import httpx
    from openai import APITimeoutError
    calls = spark_and_wonder(_verdict_json(risk="WASPADA", category="lainnya", confidence=0.5, alert_guardian=False),
                             wonder_exc=APITimeoutError(request=httpx.Request("POST", "http://x")))
    r = orchestrator.analyze(Input(type="text", text="halo bu"))
    assert calls == ["meta", "wonder"] and r.verdict.risk == "WASPADA" and r.provider == "meta"
    assert r.escalation["wonder"] is None


# --------------------------------------------------------------------------- SAM 3.1 crop for hard QR photos

def test_sam_parse_boxes():
    from app.tools import segment
    text = ("<0f>0<|box;x1=126;y1=335;x2=634;y2=845;w=760;h=1080;c=0.95|><|mask;x=0;y=0;data=1,1,!x|>,"
            "1<|box;x1=1;y1=2;x2=3;y2=4;w=760;h=1080;c=0.2|><|mask;data=1,1,!x|>")
    boxes = segment.parse_boxes(text)
    assert boxes[0]["x1"] == 126 and boxes[0]["c"] == 0.95 and len(boxes) == 2
    assert segment.parse_boxes("") == []


def test_sam_crop_rescues_missed_qr(fake, monkeypatch):
    """Local scan misses the QR; the model calls decode_qris; SAM finds it; the crop decodes."""
    from app.tools import segment
    big = cv2.copyMakeBorder(cv2.imdecode(np.frombuffer(qr_png(make_qris("ANDI CELL")), np.uint8), cv2.IMREAD_COLOR),
                             600, 600, 600, 600, cv2.BORDER_CONSTANT, value=(255, 255, 255))
    data = cv2.imencode(".png", big)[1].tobytes()
    h, w = big.shape[:2]
    real_decode = qris.decode_qr_image
    monkeypatch.setattr(qris, "decode_qr_image", lambda b: None if b == data else real_decode(b))  # "missed"
    monkeypatch.setattr(segment, "find_boxes", lambda b, phrase: [{"x1": 550, "y1": 550, "x2": w - 550, "y2": h - 550, "c": 0.9}])
    monkeypatch.setattr(orchestrator, "settings", SimpleNamespace(meta_api_key="k", max_tool_steps=4, llm_provider="meta",
                                                                  wonder_threshold=0))
    call = SimpleNamespace(id="c1", function=SimpleNamespace(name="decode_qris", arguments="{}"))
    p = fake([_resp(tool_calls=[call]), _resp(_verdict_json(category="qris_palsu"))])
    r = orchestrator.analyze(Input(type="image", text="Warung Bu Sri", media_bytes=data, mime="image/png"))
    assert "Ada kode QR" not in p.calls[0]["messages"][1]["content"]  # the local scan found nothing
    assert "ANDI CELL" in p.calls[1]["messages"][-1]["content"]       # but the tool answer has it
    assert r.tools_used[:2] == ["decode_qris", "sam_crop"] or "sam_crop" in r.tools_used
    assert r.tool_outputs["sam_crop"]["decoded"] is True


def test_sam_failure_is_silent(fake, monkeypatch):
    from app.tools import segment
    monkeypatch.setattr(segment, "find_boxes", lambda b, phrase: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(orchestrator, "settings", SimpleNamespace(meta_api_key="k", max_tool_steps=4, llm_provider="meta",
                                                                  wonder_threshold=0))
    call = SimpleNamespace(id="c1", function=SimpleNamespace(name="decode_qris", arguments="{}"))
    p = fake([_resp(tool_calls=[call]), _resp(_verdict_json(risk="WASPADA", category="lainnya", alert_guardian=False))])
    r = orchestrator.analyze(Input(type="image", media_bytes=b"not-an-image-with-qr", mime="image/png"))
    assert '"found": false' in p.calls[1]["messages"][-1]["content"] and r.verdict.risk == "WASPADA"
