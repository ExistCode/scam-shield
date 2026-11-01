"""/wa demo channel: same flow as Telegram/WhatsApp, bot messages go to an in-memory inbox per session."""
from fastapi.testclient import TestClient

from app.agent.schema import AnalysisResult, Verdict
from app.channels import web_demo
from app.main import app

BAHAYA = Verdict(risk="BAHAYA", category="minta_pulsa", confidence=0.95, scam_type="impersonation",
                 reason="Nomor baru minta transfer darurat.", next_steps=["Jangan transfer dulu.", "Telepon Hans."],
                 alert_guardian=True, reply_text="Ini penipuan, Mama.")


def _client(monkeypatch):
    monkeypatch.setattr(web_demo, "analyze_with_deadline", lambda inp: AnalysisResult(verdict=BAHAYA, provider="meta",
                                                                        model="muse-spark-1.3"))
    return TestClient(app)


def _inbox(c, session, who):
    return [m["text"] for m in c.get(f"/demo/inbox?session={session}").json()["messages"]
            if m["to"].startswith(who) and not m.get("out")]


def test_onboarding_link_verdict_and_guardian_phone(monkeypatch):
    c = _client(monkeypatch)
    s = "sessionAAAA1"

    def say(text, forwarded=False, i=[0]):
        i[0] += 1
        assert c.post("/demo/send", json={"session": s, "id": f"m{i[0]}", "text": text, "forwarded": forwarded}).status_code == 200

    say("Halo")
    assert "mau aku panggil apa" in _inbox(c, s, "parent")[-1]
    say("Mama")
    assert "aku panggil *Mama*" in _inbox(c, s, "parent")[-1]
    say("daftar wali anak Hans")
    assert "Hans (anak) sekarang ikut jagain Mama" in _inbox(c, s, "parent")[-1]
    assert "Mama daftarin kamu sebagai *wali*" in _inbox(c, s, "g:")[-1]

    say("Ma, ini nomor baru aku, transfer 2 juta ya", forwarded=True)
    parent = _inbox(c, s, "parent")
    assert parent[-2].startswith("🔎 Sedang dicek")
    assert "Hans udah aku kabarin ya, Mama." in parent[-1] and "Ibu/Bapak" not in parent[-1]
    alert = _inbox(c, s, "g:")[-1]
    assert alert == ("🔴 Hans, Mama barusan dapet chat dari nomor baru yang ngaku keluarga, minta transfer darurat. "
                     "Kayaknya penipuan. Telepon Mama sekarang ya.")


def test_setup_prepares_a_clean_demo(monkeypatch):
    c = _client(monkeypatch)
    s = "sessionBBBB2"
    assert c.post("/demo/setup", json={"session": s}).status_code == 200
    assert _inbox(c, s, "") == []  # no chat history
    c.post("/demo/send", json={"session": s, "id": "x1", "text": "Ma, transfer dulu ya", "forwarded": True})
    assert "Hans udah aku kabarin ya, Mama." in _inbox(c, s, "parent")[-1]


def test_assets_are_whitelisted():
    c = TestClient(app)
    assert c.get("/demo/assets/qris_warung_foto.jpg").status_code == 200
    assert c.get("/demo/assets/..%2F..%2F..%2F.env").status_code == 404
    assert c.get("/demo/assets/Cek_Resi_JNT.apk").status_code == 404
    assert c.get("/demo/inbox?session=bad").status_code == 400


def test_inbox_keeps_parent_messages_for_reload(monkeypatch):
    """After a page reload the demo replays the session, including what Mama sent (no media stored)."""
    c = _client(monkeypatch)
    s = "sessionCCCC3"
    c.post("/demo/setup", json={"session": s})
    c.post("/demo/send", json={"session": s, "id": "m1", "type": "document", "text": "Kami harap kehadirannya",
                               "filename": "Undangan.apk", "forwarded": True})
    msgs = c.get(f"/demo/inbox?session={s}").json()["messages"]
    out = [m for m in msgs if m.get("out")]
    assert out == [{**out[0], "cid": "m1", "type": "document", "text": "Kami harap kehadirannya",
                    "filename": "Undangan.apk", "forwarded": True, "to": "parent"}]
    assert "media_base64" not in out[0]
    assert any("Kayaknya penipuan" in m["text"] for m in msgs if m["to"].startswith("g:"))  # Hans's alert is kept too
