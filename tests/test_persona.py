"""Personal voice: address only by panggilan, name the real family member, never "Ibu/Bapak"."""
import json
import os
import re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.agent import followup, orchestrator, persona
from app.agent.schema import AnalysisResult, Input, Verdict
from app.channels import telegram_bot, telegram_webhook
from app.channels.formatting import format_reply, guardian_line

PROFILE = {"panggilan": "Mama", "bahasa_style": "santai", "contacts": [{"name": "Hans", "relation": "anak"}]}

# What a model might write if it ignored the profile: the safety net must still fix it.
FORMAL = Verdict(risk="BAHAYA", category="aparat_palsu", confidence=0.95, scam_type="fake_police",
                 reason="Polisi asli tidak pernah minta transfer lewat telepon.",
                 next_steps=["Tutup teleponnya.", "Jangan transfer apa pun.", "Ceritakan ke anak atau keluarga Ibu/Bapak."],
                 alert_guardian=True, reply_text="Ibu/Bapak, ini penipuan. Jangan diikuti ya.")


def _assert_personal(text: str):
    assert "Mama" in text, text
    assert "Hans" in text, text
    assert not re.search(r"Ibu\s*/\s*Bapak|Bapak\s*/\s*Ibu", text), text


# --------------------------------------------------------------------------- 1. verdict (agent + format)

def test_verdict_reply_is_personal(monkeypatch):
    calls = []

    class P:
        name, model, supports_images = "meta", "muse-spark-1.3", False

        def chat(self, messages, tools=None, response_format=None, **kw):
            calls.append(messages)
            msg = SimpleNamespace(content=FORMAL.model_dump_json(), tool_calls=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    monkeypatch.setattr(orchestrator, "get_provider", lambda name=None: P())
    monkeypatch.setattr(orchestrator, "fallback_name", lambda primary: None)
    r = orchestrator.analyze(Input(type="text", text="Kami dari Polda, transfer untuk pembuktian", profile=PROFILE))

    system = calls[0][0]["content"]
    assert 'HANYA dengan "Mama"' in system and "Hans (anak)" in system and "Telepon Hans sekarang ya, Mama" in system

    out = format_reply(r.verdict, guardian_line("sent", "/daftar_wali", ["Hans"], PROFILE), profile=PROFILE)
    _assert_personal(out)
    assert out.splitlines()[0] == "🔴 *BAHAYA* · Mama, ini penipuan. Jangan diikuti ya."
    assert "Alasannya" not in out and "✅ Hans udah aku kabarin ya, Mama." in out
    assert "Ceritakan ke anak atau keluarga Mama." in out


# --------------------------------------------------------------------------- 2. follow-up

def test_followup_reply_is_personal(monkeypatch):
    seen = {}

    def chat(messages, tools=None, response_format=None):
        seen["system"] = messages[0]["content"]
        reply = "Tenang dulu ya Ibu/Bapak. NIK aja nggak cukup buat ambil uang. Jangan kasih OTP, dan cerita ke Hans ya."
        msg = SimpleNamespace(content=json.dumps({"intent": "followup", "reply": reply, "already_paid": False}))
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    monkeypatch.setattr(followup, "get_provider", lambda name=None: SimpleNamespace(chat=chat))
    last = {"case_id": 1, "risk": "BAHAYA", "category": "cs_bank_palsu", "reason": "minta OTP",
            "next_steps": ["Jangan kirim OTP."], "input_text": "CS BCA minta OTP"}
    ans = followup.answer("dia punya NIK ku", last, PROFILE)
    assert 'HANYA dengan "Mama"' in seen["system"] and "dia punya NIK ku" in seen["system"]
    _assert_personal(ans.reply)


# --------------------------------------------------------------------------- 3. end to end on Telegram

def test_telegram_onboarding_and_personal_verdict(monkeypatch):
    from app.main import app

    sent = []
    monkeypatch.setattr(telegram_bot, "send_text", lambda chat_id, text, reply_to=None, **kw: sent.append((str(chat_id), text)))
    monkeypatch.setattr(telegram_bot, "send_typing", lambda chat_id: None)
    monkeypatch.setattr(telegram_bot, "bot_username", lambda: "RondaBot")
    seen_profiles = []

    def fake_analyze(inp):
        seen_profiles.append(inp.profile)
        return AnalysisResult(verdict=FORMAL.model_copy(update={"contacts": []}), provider="meta", model="muse-spark-1.3")

    monkeypatch.setattr(telegram_webhook, "analyze_with_deadline", fake_analyze)
    MAMA, HANS = 5581001, 5581002
    uid = iter(range(7000, 8000))
    client = TestClient(app)

    def post(chat_id, text, first="Sri", forwarded=False):
        m = {"message_id": next(uid), "chat": {"id": chat_id, "type": "private"},
             "from": {"id": chat_id, "first_name": first}, "text": text}
        if forwarded:
            m["forward_origin"] = {"type": "hidden_user", "sender_user_name": "Polda"}
        client.post("/telegram/webhook", json={"update_id": next(uid), "message": m},
                    headers={"X-Telegram-Bot-Api-Secret-Token": "tg-secret"})
        return [t for c, t in sent if c == str(chat_id)][-1]

    assert "mau aku panggil apa" in post(MAMA, "/start")
    assert "aku panggil *Mama*" in post(MAMA, "panggil aja Mama")
    invite = post(MAMA, "/daftar_wali anak")
    assert "start=wali_" in invite and "_a_" in invite
    post(HANS, "/start " + invite.split("?start=")[1].split()[0], first="Hans")
    assert any(c == str(MAMA) and "*Hans* (anak) sekarang ikut jagain Mama" in t for c, t in sent)

    reply = post(MAMA, "Kami dari Polda, transfer untuk pembuktian", forwarded=True)
    assert seen_profiles[-1]["panggilan"] == "Mama"
    assert seen_profiles[-1]["contacts"][0]["name"] == "Hans" and seen_profiles[-1]["contacts"][0]["relation"] == "anak"
    _assert_personal(reply)
    assert "Hans udah aku kabarin ya, Mama." in reply

    assert "aku panggil *Bu Sri*" in post(MAMA, "/panggil Bu Sri")
    assert "Bu Sri" in post(MAMA, "/status") and "Hans (anak)" in post(MAMA, "/status")


# --------------------------------------------------------------------------- helpers

@pytest.mark.parametrize("text,expected", [
    ("Mama", "Mama"), ("panggil aja bu sri", "Bu Sri"), ("Om Budi.", "Om Budi"), ("nama saya Oma", "Oma"),
    ("makasih", None), ("ok", None), ("0812345678", None), ("Paket Anda tertahan di gudang silakan cek", None),
])
def test_parse_panggilan(text, expected):
    assert persona.parse_panggilan(text) == expected


def test_invite_relation_is_signed():
    tok = telegram_webhook.make_invite(42, "cucu")
    assert telegram_webhook.parse_invite_full(tok) == (42, "cucu")
    assert telegram_webhook.parse_invite_full(tok.replace("_c_", "_a_")) is None  # can't swap the relation
    assert telegram_webhook.parse_invite_full(telegram_webhook.make_invite(42)) == (42, "keluarga")


def test_no_panggilan_drops_ibu_bapak():
    assert persona.address("Ibu/Bapak, ini penipuan.", None) == "Ini penipuan."
    assert persona.address("Ini penipuan, Ibu/Bapak.", None) == "Ini penipuan."
    assert persona.address("nomor lama yang Ibu/Bapak simpan", None) == "nomor lama yang kamu simpan"


# --------------------------------------------------------------------------- live (Muse Spark)

live = pytest.mark.skipif(not (os.environ.get("RUN_LIVE") and os.environ.get("META_API_KEY")),
                          reason="set RUN_LIVE=1 and META_API_KEY to run against Muse Spark")
LIVE_CASES = [
    "Selamat siang, kami dari Polda Metro Jaya. Nama Anda tercatat sebagai penerima paket narkoba. Jangan tutup "
    "telepon dan jangan beritahu keluarga. Transfer dana ke rekening penampungan PPATK untuk pembuktian.",
    "Ma, ini nomor baru aku, hp lama rusak. Tolong transfer 2 juta dulu ya, darurat banget. Jangan bilang siapa-siapa.",
]


@live
@pytest.mark.parametrize("text", LIVE_CASES, ids=["polisi", "nomor_baru"])
def test_live_personal_voice(text):
    r = orchestrator.analyze(Input(type="text", text=text, profile=PROFILE))
    out = format_reply(r.verdict, guardian_line("sent", "/daftar_wali", ["Hans"], PROFILE), profile=PROFILE)
    print("\n" + out)
    _assert_personal(out)
    model_text = " ".join([r.verdict.reply_text, r.verdict.reason, *r.verdict.next_steps])
    assert "Mama" in r.verdict.reply_text, "Muse should use the panggilan itself"
    assert not re.search(r"\b(Ibu|Bapak|Anda)\b", model_text), model_text
    assert "Hans" in " ".join(r.verdict.next_steps + [r.verdict.reply_text]), "Muse should name the family member"


@pytest.mark.parametrize("text,expected", [
    ("Mama", "Mama"), ("bu sri", "Bu Sri"), ("Om Budi", "Om Budi"), ("Sri", "Sri"), ("panggil aja Oma", "Oma"),
    ("terus gimana", None), ("terus gimana?", None), ("beneran?", None), ("beneran", None),
    ("aduh takut", None), ("dia punya NIK ku", None), ("aku udah transfer", None), ("jadi aman", None),
])
def test_parse_panggilan_strict_after_a_verdict(text, expected):
    """Right after a verdict an answer to "mau aku panggil apa?" must not swallow follow-up questions."""
    assert persona.parse_panggilan(text, strict=True) == expected
