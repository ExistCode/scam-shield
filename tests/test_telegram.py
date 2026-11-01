import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.agent.schema import AnalysisResult, Verdict
from app.channels import telegram_bot, telegram_webhook
from app.channels.formatting import format_reply, to_telegram_html

BAHAYA = Verdict(risk="BAHAYA", category="kurir_apk", confidence=0.95, reason="Ada file APK.",
                 next_steps=["Jangan buka filenya."], alert_guardian=True, reply_text="Ini penipuan, Bu.")


def test_invite_roundtrip_and_tamper():
    tok = telegram_webhook.make_invite(42)
    assert len(tok) <= 64 and telegram_webhook.parse_invite(tok) == 42
    assert telegram_webhook.parse_invite(tok.replace("_42_", "_43_")) is None
    assert telegram_webhook.parse_invite("wali_42_deadbeefdeadbeef") is None
    assert telegram_webhook.parse_invite("garbage") is None


def test_html_escapes_and_formats():
    out = to_telegram_html("*BAHAYA* <script>x</script> kategori kurir_apk _Cek dulu._")
    assert "<b>BAHAYA</b>" in out and "&lt;script&gt;" in out
    assert "kurir_apk" in out and "<i>Cek dulu.</i>" in out
    assert "<b>" in to_telegram_html(format_reply(BAHAYA))


def test_build_input_apk_and_image_document(monkeypatch):
    inp, kind = telegram_webhook._build_input(
        {"document": {"file_id": "f", "file_name": "Cek_Resi.apk", "mime_type": "application/vnd.android.package-archive"},
         "caption": "paket tertahan"}, user_id=1)
    assert inp.type == "document" and ".apk" in inp.text and kind == "text"
    monkeypatch.setattr(telegram_bot, "download_file", lambda fid: b"img")
    inp, kind = telegram_webhook._build_input({"document": {"file_id": "f", "mime_type": "image/png"}}, user_id=1)
    assert inp.type == "image" and inp.media_bytes == b"img" and kind == "image"


@pytest.fixture
def client():
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_rejects_bad_secret(client):
    r = client.post("/telegram/webhook", json={"update_id": 1}, headers={"X-Telegram-Bot-Api-Secret-Token": "nope"})
    assert r.status_code == 401


def test_guardian_link_and_alert(client, monkeypatch):
    sent = []
    monkeypatch.setattr(telegram_bot, "send_text", lambda chat_id, text, reply_to=None: sent.append((str(chat_id), text)))
    monkeypatch.setattr(telegram_bot, "send_typing", lambda chat_id: None)
    monkeypatch.setattr(telegram_bot, "bot_username", lambda: "CekDuluBot")
    monkeypatch.setattr(telegram_webhook, "analyze", lambda inp: AnalysisResult(verdict=BAHAYA, provider="fake", model="fake"))

    uid = iter(range(1000, 2000))
    PARENT, CHILD = 5550001, 5550002

    def post(chat_id, text, first="Sri"):
        upd = {"update_id": next(uid), "message": {"message_id": next(uid), "chat": {"id": chat_id, "type": "private"},
               "from": {"id": chat_id, "first_name": first}, "text": text}}
        r = client.post("/telegram/webhook", json=upd, headers={"X-Telegram-Bot-Api-Secret-Token": "tg-secret"})
        assert r.status_code == 200

    post(PARENT, "/start")
    assert sum("Cek Dulu" in t for c, t in sent if c == str(PARENT)) == 1  # welcome only once

    post(PARENT, "/daftar_wali")
    link_msg = sent[-1][1]
    assert "https://t.me/CekDuluBot?start=wali_" in link_msg
    payload = link_msg.split("?start=")[1].split()[0]

    post(CHILD, f"/start {payload}", first="Hans")
    assert any(c == str(CHILD) and "sekarang *wali*" in t for c, t in sent)
    assert any(c == str(PARENT) and "Hans" in t and "jadi wali" in t for c, t in sent)

    post(PARENT, "Paket Anda tertahan, install Cek_Resi.apk")
    assert any(c == str(PARENT) and "*BAHAYA*" in t for c, t in sent)
    alerts = [t for c, t in sent if c == str(CHILD) and "Peringatan" in t]
    assert len(alerts) == 1 and "Sri" in alerts[0]

    post(PARENT, "/status")
    assert "Hans" in sent[-1][1]


def test_first_message_gets_verdict_before_intro(client, monkeypatch):
    sent = []
    monkeypatch.setattr(telegram_bot, "send_text", lambda chat_id, text, reply_to=None: sent.append(text))
    monkeypatch.setattr(telegram_bot, "send_typing", lambda chat_id: None)
    monkeypatch.setattr(telegram_webhook, "analyze", lambda inp: AnalysisResult(verdict=BAHAYA, provider="fake", model="fake"))
    upd = {"update_id": 9001, "message": {"message_id": 9002, "chat": {"id": 5559999, "type": "private"},
           "from": {"id": 5559999, "first_name": "Ibu"}, "text": "Paket tertahan, buka aplikasi ini"}}
    client.post("/telegram/webhook", json=upd, headers={"X-Telegram-Bot-Api-Secret-Token": "tg-secret"})
    assert len(sent) == 3
    assert "Sedang dicek" in sent[0]                  # placeholder right away
    assert "*BAHAYA*" in sent[1]                      # then the answer
    assert "Cek Dulu" in sent[2] and "setuju" in sent[2]  # intro + consent after
    assert "Halo, saya" not in sent[1]


def test_tools_footer():
    from app.channels.formatting import tools_footer
    r = AnalysisResult(verdict=BAHAYA, provider="meta", model="muse-spark-1.3",
                       tools_used=["transcribe_audio", "check_link", "safe_word_check"],
                       overrides=[{"rule": "apk_selalu_bahaya", "field": "risk", "from": "WASPADA", "to": "BAHAYA"}])
    assert tools_footer(r) == ("🔎 _Dicek dengan: Muse Voice → Muse Spark → cek link → cek kata aman"
                               " → aturan keamanan_")
    r = AnalysisResult(verdict=BAHAYA, provider="fireworks", model="accounts/fireworks/models/llama4-maverick")
    assert "llama4-maverick (cadangan)" in tools_footer(r)
    assert "<i>" in to_telegram_html(tools_footer(r))


def _followup_provider(payload: dict):
    def chat(messages, tools=None, response_format=None):
        msg = SimpleNamespace(content=json.dumps(payload), tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])
    return SimpleNamespace(chat=chat)


def test_followup_answered_in_context(client, monkeypatch):
    from app.agent import followup
    sent, analyzed = [], []
    monkeypatch.setattr(telegram_bot, "send_text", lambda chat_id, text, reply_to=None: sent.append((str(chat_id), text)))
    monkeypatch.setattr(telegram_bot, "send_typing", lambda chat_id: None)
    monkeypatch.setattr(telegram_bot, "bot_username", lambda: "CekDuluBot")
    monkeypatch.setattr(telegram_webhook, "analyze",
                        lambda inp: analyzed.append(inp.text) or AnalysisResult(verdict=BAHAYA, provider="fake", model="fake"))
    PARENT, CHILD = 5561001, 5561002
    uid = iter(range(3000, 4000))

    def post(chat_id, text, first="Sri", forwarded=False):
        m = {"message_id": next(uid), "chat": {"id": chat_id, "type": "private"},
             "from": {"id": chat_id, "first_name": first}, "text": text}
        if forwarded:
            m["forward_origin"] = {"type": "hidden_user", "sender_user_name": "Kurir"}
        client.post("/telegram/webhook", json={"update_id": next(uid), "message": m},
                    headers={"X-Telegram-Bot-Api-Secret-Token": "tg-secret"})

    # set up guardian, then a first verdict
    post(PARENT, "/daftar_wali")
    post(CHILD, "/start " + sent[-1][1].split("?start=")[1].split()[0], first="Hans")
    post(PARENT, "Paket tertahan, buka aplikasi ini", forwarded=True)
    assert len(analyzed) == 1

    # "terus gimana?" -> follow-up, not a new analysis
    monkeypatch.setattr(followup, "get_provider", lambda name=None: _followup_provider(
        {"intent": "followup", "reply": "Hapus saja pesannya ya Bu, jangan dibuka.", "already_paid": False}))
    post(PARENT, "terus gimana?")
    assert len(analyzed) == 1 and sent[-1] == (str(PARENT), "Hapus saja pesannya ya Bu, jangan dibuka.")

    # "aku sudah transfer" -> emergency reply + guardian alert (no cooldown)
    monkeypatch.setattr(followup, "get_provider", lambda name=None: _followup_provider(
        {"intent": "followup", "reply": "Segera telepon bank ya Bu.", "already_paid": True}))
    n_child = sum(1 for c, _ in sent if c == str(CHILD))
    post(PARENT, "aduh aku sudah transfer 2 juta")
    assert any(c == str(CHILD) and "SUDAH terlanjur" in t for c, t in sent[n_child:])
    emergency = [t for c, t in sent if c == str(PARENT)][-1]
    assert emergency.startswith("Segera telepon bank ya Bu.")
    assert "1500888" in emergency and "https://iasc.ojk.go.id" in emergency  # added by code, not the model

    # model says it's new content -> normal analysis
    monkeypatch.setattr(followup, "get_provider", lambda name=None: _followup_provider(
        {"intent": "new_check", "reply": "", "already_paid": False}))
    post(PARENT, "Selamat anda menang undian mobil")
    assert len(analyzed) == 2

    # forwarded messages never go through the follow-up path
    monkeypatch.setattr(followup, "get_provider", lambda name=None: (_ for _ in ()).throw(AssertionError("called")))
    post(PARENT, "ok", forwarded=True)
    assert len(analyzed) == 3


def test_build_input_video_and_video_note(monkeypatch):
    monkeypatch.setattr(telegram_bot, "download_file", lambda fid: b"mp4")
    inp, kind = telegram_webhook._build_input({"video": {"file_id": "v", "mime_type": "video/mp4"}, "caption": "polisi"}, 1)
    assert inp.type == "video" and kind == "voice" and inp.media_bytes == b"mp4"
    inp, _ = telegram_webhook._build_input({"video_note": {"file_id": "n"}}, 1)
    assert inp.type == "video"
    inp, _ = telegram_webhook._build_input({"document": {"file_id": "d", "mime_type": "video/quicktime"}}, 1)
    assert inp.type == "video"


def test_reply_only_claims_guardian_alert_when_sent(client, monkeypatch):
    sent = []
    monkeypatch.setattr(telegram_bot, "send_text", lambda chat_id, text, reply_to=None: sent.append((str(chat_id), text)))
    monkeypatch.setattr(telegram_bot, "send_typing", lambda chat_id: None)
    monkeypatch.setattr(telegram_bot, "bot_username", lambda: "CekDuluBot")
    monkeypatch.setattr(telegram_webhook, "analyze", lambda inp: AnalysisResult(verdict=BAHAYA, provider="fake", model="fake"))
    PARENT, CHILD = 5571001, 5571002
    uid = iter(range(5000, 6000))

    def post(chat_id, text, first="Sri"):
        m = {"message_id": next(uid), "chat": {"id": chat_id, "type": "private"},
             "from": {"id": chat_id, "first_name": first}, "text": text, "forward_origin": {"type": "hidden_user"}}
        client.post("/telegram/webhook", json={"update_id": next(uid), "message": m},
                    headers={"X-Telegram-Bot-Api-Secret-Token": "tg-secret"})

    def verdict_msgs():
        return [t for c, t in sent if c == str(PARENT) and "*BAHAYA*" in t]

    post(PARENT, "/start")
    post(PARENT, "Paket tertahan, buka aplikasi ini")      # no guardian yet
    assert "kabari" not in verdict_msgs()[-1]
    assert "Belum ada wali terdaftar, ketik /daftar_wali" in verdict_msgs()[-1]

    post(PARENT, "/daftar_wali")
    post(CHILD, "/start " + sent[-1][1].split("?start=")[1].split()[0], first="Hans")
    post(PARENT, "Paket tertahan lagi, buka aplikasi ini")  # guardian alerted
    assert "✅ Sudah kami kabari: Hans" in verdict_msgs()[-1]
    assert "Belum ada wali" not in verdict_msgs()[-1]
    post(PARENT, "Paket tertahan ketiga kali")               # cooldown: say so, don't re-alert
    assert "Sudah kami kabari beberapa menit lalu: Hans" in verdict_msgs()[-1]

    monkeypatch.setattr(telegram_bot, "send_guardian_alert", lambda *a: (_ for _ in ()).throw(RuntimeError("down")))
    from app.agent import guardian_policy
    monkeypatch.setattr(guardian_policy, "COOLDOWN", guardian_policy.timedelta(0))
    post(PARENT, "Paket tertahan keempat")                   # delivery failed: say so
    assert "belum terkirim" in verdict_msgs()[-1] and "Sudah kami kabari" not in verdict_msgs()[-1]


def test_followup_scrubs_invented_numbers(monkeypatch):
    from app.agent import contacts, followup
    monkeypatch.setattr(followup, "get_provider", lambda name=None: _followup_provider(
        {"intent": "followup", "reply": "Telepon Mandiri 14000 atau WA 0812-9999-8888 ya Bu.", "already_paid": True}))
    ans = followup.answer("sudah transfer", {"case_id": 1, "risk": "BAHAYA", "category": "cs_bank_palsu",
                                             "reason": "x", "next_steps": [], "input_text": "CS bank"})
    assert contacts.unofficial_numbers(ans.reply) == [] and "14000" not in ans.reply
    assert "1500888" in ans.reply and "iasc.ojk.go.id" in ans.reply
