import re
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
    monkeypatch.setattr(telegram_bot, "send_text", lambda chat_id, text, reply_to=None, **kw: sent.append((str(chat_id), text)))
    monkeypatch.setattr(telegram_bot, "send_typing", lambda chat_id: None)
    monkeypatch.setattr(telegram_bot, "bot_username", lambda: "RondaBot")
    monkeypatch.setattr(telegram_webhook, "analyze", lambda inp: AnalysisResult(verdict=BAHAYA, provider="fake", model="fake"))

    uid = iter(range(1000, 2000))
    PARENT, CHILD = 5550001, 5550002

    def post(chat_id, text, first="Sri"):
        upd = {"update_id": next(uid), "message": {"message_id": next(uid), "chat": {"id": chat_id, "type": "private"},
               "from": {"id": chat_id, "first_name": first}, "text": text}}
        r = client.post("/telegram/webhook", json=upd, headers={"X-Telegram-Bot-Api-Secret-Token": "tg-secret"})
        assert r.status_code == 200

    post(PARENT, "/start")
    assert sum("Ronda" in t for c, t in sent if c == str(PARENT)) == 1  # welcome only once

    post(PARENT, "/daftar_wali")
    link_msg = sent[-1][1]
    assert "https://t.me/RondaBot?start=wali_" in link_msg
    payload = link_msg.split("?start=")[1].split()[0]

    post(CHILD, f"/start {payload}", first="Hans")
    assert any(c == str(CHILD) and "jadi *wali*" in t for c, t in sent)
    assert any(c == str(PARENT) and "Hans" in t and "ikut jagain" in t for c, t in sent)

    post(PARENT, "Paket Anda tertahan, install Cek_Resi.apk")
    assert any(c == str(PARENT) and "*BAHAYA*" in t for c, t in sent)
    alerts = [t for c, t in sent if c == str(CHILD) and "barusan dapet pesan BAHAYA" in t]
    assert len(alerts) == 1 and alerts[0].startswith("🔴 Hans, Sri barusan dapet pesan BAHAYA")

    post(PARENT, "/status")
    assert "Hans" in sent[-1][1]


def test_first_message_gets_verdict_before_intro(client, monkeypatch):
    sent = []
    monkeypatch.setattr(telegram_bot, "send_text", lambda chat_id, text, reply_to=None, **kw: sent.append(text))
    monkeypatch.setattr(telegram_bot, "send_typing", lambda chat_id: None)
    monkeypatch.setattr(telegram_webhook, "analyze", lambda inp: AnalysisResult(verdict=BAHAYA, provider="fake", model="fake"))
    upd = {"update_id": 9001, "message": {"message_id": 9002, "chat": {"id": 5559999, "type": "private"},
           "from": {"id": 5559999, "first_name": "Ibu"}, "text": "Paket tertahan, buka aplikasi ini"}}
    client.post("/telegram/webhook", json=upd, headers={"X-Telegram-Bot-Api-Secret-Token": "tg-secret"})
    assert len(sent) == 3
    assert "Sedang dicek" in sent[0]                  # placeholder right away
    assert "*BAHAYA*" in sent[1]                      # then the answer
    assert sent[2] == "Oh ya, mau aku panggil apa? (misalnya: Mama, Bu Sri, Om Budi)"  # only the question
    assert "setuju" not in sent[2] and "Aku *Ronda*" not in sent[2]

    upd = {"update_id": 9003, "message": {"message_id": 9004, "chat": {"id": 5559999, "type": "private"},
           "from": {"id": 5559999, "first_name": "Ibu"}, "text": "Mama"}}
    client.post("/telegram/webhook", json=upd, headers={"X-Telegram-Bot-Api-Secret-Token": "tg-secret"})
    assert "aku panggil *Mama*" in sent[3] and "setuju" in sent[3]  # privacy line comes with the answer


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
    monkeypatch.setattr(telegram_bot, "send_text", lambda chat_id, text, reply_to=None, **kw: sent.append((str(chat_id), text)))
    monkeypatch.setattr(telegram_bot, "send_typing", lambda chat_id: None)
    monkeypatch.setattr(telegram_bot, "bot_username", lambda: "RondaBot")
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
    assert any(c == str(CHILD) and t.startswith("🚨 Hans, Sri bilang udah terlanjur transfer") for c, t in sent[n_child:])
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
    monkeypatch.setattr(telegram_bot, "send_text", lambda chat_id, text, reply_to=None, **kw: sent.append((str(chat_id), text)))
    monkeypatch.setattr(telegram_bot, "send_typing", lambda chat_id: None)
    monkeypatch.setattr(telegram_bot, "bot_username", lambda: "RondaBot")
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
    assert "udah aku kabarin" not in verdict_msgs()[-1]
    assert "Belum ada keluarga yang didaftarin. Ketik /daftar_wali" in verdict_msgs()[-1]

    post(PARENT, "/daftar_wali")
    post(CHILD, "/start " + sent[-1][1].split("?start=")[1].split()[0], first="Hans")
    post(PARENT, "Paket tertahan lagi, buka aplikasi ini")  # guardian alerted
    assert "✅ Hans udah aku kabarin ya." in verdict_msgs()[-1]
    assert "Belum ada" not in verdict_msgs()[-1]
    post(PARENT, "Paket tertahan ketiga kali")               # cooldown: say so, don't re-alert
    assert "Hans udah aku kabarin tadi ya." in verdict_msgs()[-1]

    monkeypatch.setattr(telegram_bot, "send_guardian_alert", lambda *a: (_ for _ in ()).throw(RuntimeError("down")))
    from app.agent import guardian_policy
    monkeypatch.setattr(guardian_policy, "COOLDOWN", guardian_policy.timedelta(0))
    post(PARENT, "Paket tertahan keempat")                   # delivery failed: say so
    assert "belum berhasil ngabarin Hans" in verdict_msgs()[-1] and "udah aku kabarin" not in verdict_msgs()[-1]


def test_followup_scrubs_invented_numbers(monkeypatch):
    from app.agent import contacts, followup
    monkeypatch.setattr(followup, "get_provider", lambda name=None: _followup_provider(
        {"intent": "followup", "reply": "Telepon Mandiri 14000 atau WA 0812-9999-8888 ya Bu.", "already_paid": True}))
    ans = followup.answer("sudah transfer", {"case_id": 1, "risk": "BAHAYA", "category": "cs_bank_palsu",
                                             "reason": "x", "next_steps": [], "input_text": "CS bank"})
    assert contacts.unofficial_numbers(ans.reply) == [] and "14000" not in ans.reply
    assert "1500888" in ans.reply and "iasc.ojk.go.id" in ans.reply


# --------------------------------------------------------------------------- "🔎 Detail" button

def test_detail_button_and_trace(client, monkeypatch):
    from app.channels.formatting import format_reply as fr
    sent, html_sent, answers = [], [], []
    monkeypatch.setattr(telegram_bot, "send_text",
                        lambda chat_id, text, reply_to=None, reply_markup=None: sent.append((str(chat_id), text, reply_markup)))
    monkeypatch.setattr(telegram_bot, "send_html", lambda chat_id, text, reply_to=None: html_sent.append((str(chat_id), text, reply_to)))
    monkeypatch.setattr(telegram_bot, "answer_callback", lambda cid, text=None: answers.append(text))
    monkeypatch.setattr(telegram_bot, "send_typing", lambda chat_id: None)

    verdict = BAHAYA.model_copy(update={"confidence": 0.93})
    result = AnalysisResult(
        verdict=verdict, provider="wonder", model="muse-wonder-1.0", latency_ms=12400, model_risk="WASPADA",
        tools_used=["transcribe_audio", "check_link"],
        tool_outputs={"transcribe_audio": {"transcript": "Ini dari <script>alert(1)</script> Polda", "duration_ms": 21000},
                      "check_link": [{"url": "http://bit.ly/<b>x</b>", "domain": "evil.xyz", "redirects": 2,
                                      "flags": ["link_pendek", "meniru_bca"]}]},
        overrides=[{"rule": "apk_selalu_bahaya", "field": "risk", "from": "WASPADA", "to": "BAHAYA"},
                   {"rule": "lapor_nomor_penipu", "field": "next_steps", "to": ["0857-1122-3344"]}],
        escalation={"reason": "low_confidence", "spark": {"risk": "WASPADA", "category": "lainnya",
                                                          "confidence": 0.55, "model": "muse-spark-1.3"},
                    "wonder": {"risk": "BAHAYA", "category": "kurir_apk", "confidence": 0.93}})
    monkeypatch.setattr(telegram_webhook, "analyze", lambda inp: result)

    OWNER, OTHER = 5591001, 5591002
    upd = {"update_id": 9901, "message": {"message_id": 77, "chat": {"id": OWNER, "type": "private"},
           "from": {"id": OWNER, "first_name": "Sri"}, "text": "Paket tertahan, install Cek_Resi.apk",
           "forward_origin": {"type": "hidden_user"}}}
    client.post("/telegram/webhook", json=upd, headers={"X-Telegram-Bot-Api-Secret-Token": "tg-secret"})

    verdict_msgs = [(t, kb) for c, t, kb in sent if "*BAHAYA*" in t]
    text, kb = verdict_msgs[-1]
    data = kb["inline_keyboard"][0][0]
    assert data["text"] == "🔎 Detail" and re.fullmatch(r"detail:\d+", data["callback_data"])
    assert "Detail pengecekan" not in text and "Langkah" not in text  # main reply unchanged
    check_id = int(data["callback_data"].split(":")[1])

    def tap(chat_id, data, uid):
        cb = {"update_id": uid, "callback_query": {"id": f"cb{uid}", "from": {"id": chat_id}, "data": data,
              "message": {"message_id": 78, "chat": {"id": chat_id, "type": "private"}}}}
        client.post("/telegram/webhook", json=cb, headers={"X-Telegram-Bot-Api-Secret-Token": "tg-secret"})

    tap(OWNER, f"detail:{check_id}", 9902)
    chat, trace, reply_to = html_sent[-1]
    assert chat == str(OWNER) and reply_to == 78 and answers[-1] is None
    assert f"Detail pengecekan #{check_id}" in trace and "keyakinan 0.93" in trace
    assert "<b>Muse Voice Transcribe</b> (audio 21 dtk)" in trace
    assert "<b>check_link</b> (http://bit.ly/&lt;b&gt;x&lt;/b&gt;) → evil.xyz (2 redirect); link_pendek, meniru_bca" in trace
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in trace and "<script>" not in trace  # escaped
    assert "Muse Spark (muse-spark-1.3): WASPADA 0.55 → Muse Wonder (muse-wonder-1.0): BAHAYA 0.93 (cek ulang)" in trace
    assert "Muse bilang WASPADA → aturan <code>apk_selalu_bahaya</code> ubah jadi <b>BAHAYA</b>" in trace
    assert "<code>lapor_nomor_penipu</code>" in trace and "12.4 dtk" in trace

    n = len(html_sent)
    tap(OTHER, f"detail:{check_id}", 9903)          # someone else's chat: nothing leaks
    tap(OWNER, "detail:999999", 9904)               # unknown check
    tap(OWNER, "detail:1;drop", 9905)               # malformed data
    assert len(html_sent) == n and answers[-3:] == ["Detailnya nggak ketemu."] * 3


def test_trace_without_tools_or_overrides():
    from app.channels.formatting import trace_html
    out = trace_html({"case_id": 5, "risk": "AMAN", "category": "lainnya", "confidence": 0.9, "model": "muse-spark-1.3",
                      "details": {"trace": [], "overrides": []}})
    assert "Muse menilai dari teksnya saja" in out and "Muse Spark (muse-spark-1.3)" in out
    assert "Tidak ada. Jawaban Muse dipakai apa adanya." in out


# --------------------------------------------------------------------------- guardian alert wording

@pytest.mark.parametrize("money_sent", [False, True])
def test_guardian_alert_is_casual_and_named(money_sent):
    from app.channels.formatting import guardian_alert_text
    v = BAHAYA.model_copy(update={"category": "cs_bank_palsu", "money_sent": money_sent,
                                  "reason": "Bank asli nggak pernah minta OTP lewat chat. Abaikan aja ya, beliau aman kalau nggak kirim kode."})
    text = guardian_alert_text("Hans", "Mama", v)
    for banned in ("Cek Dulu", "Mohon", "beliau"):
        assert banned.lower() not in text.lower(), (banned, text)
    if money_sent:
        assert text.startswith("🚨 Hans, Mama bilang udah terlanjur transfer")
    else:
        assert text == ("🔴 Hans, Mama barusan dapet pesan BAHAYA (CS bank palsu). "
                        "Alasannya: Bank asli nggak pernah minta OTP lewat chat. "
                        "Telepon Mama sekarang ya, sebelum ada transfer.")


def test_guardian_alert_never_says_beliau_even_if_the_reason_does():
    from app.channels.formatting import guardian_alert_text
    v = BAHAYA.model_copy(update={"reason": "Pesan ini minta beliau transfer ke rekening pribadi."})
    text = guardian_alert_text("Hans", "Mama", v)
    assert "beliau" not in text and "minta Mama transfer" in text


def test_whatsapp_guardian_template_params(monkeypatch):
    from app.channels import whatsapp_sender
    posted = []
    monkeypatch.setattr(whatsapp_sender, "_post", lambda payload: posted.append(payload) or {})
    whatsapp_sender.send_guardian_alert("6281200000000", "Hans", "Mama",
                                        BAHAYA.model_copy(update={"reason": "Ada file APK."}))
    params = [p["text"] for p in posted[0]["template"]["components"][0]["parameters"]]
    assert params == ["Hans", "Mama", "aplikasi kurir palsu (APK)", "Ada file APK", "Mama"]
    for p in params:
        assert not any(b in p for b in ("Cek Dulu", "Mohon", "beliau"))
