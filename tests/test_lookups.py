"""Public AduanNomor.id lookup (optional extra), cekrekening.id links, and our own report counts."""
import json
from types import SimpleNamespace

import httpx
import pytest

from app.agent import contacts, orchestrator
from app.agent.schema import AnalysisResult, Input, Verdict
from app.channels.formatting import format_reply, tools_footer
from app.db import repo
from app.tools import aduannomor


def _body(count, kinds=()):
    return {"status": 200, "error": False, "messages": "Data Loaded",
            "data": {"reported_phone": "+6285711223344", "reported_phone_status": "Blocked | Recycled | Aktif",
                     "reported_count": count, "reported_kejahatan": list(kinds), "reported_status_laporan": []}}


@pytest.fixture
def live_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("ADUANNOMOR_MODE", "live")
    fixtures = tmp_path / "fx.json"
    fixtures.write_text("{}")
    monkeypatch.setattr(aduannomor, "FIXTURES", fixtures)
    aduannomor._cache.clear()
    yield fixtures
    aduannomor._cache.clear()


def _fake_post(monkeypatch, response=None, exc=None, calls=None):
    def post(url, data=None, headers=None, timeout=None):
        if calls is not None:
            calls.append({"url": url, "data": data, "headers": headers, "timeout": timeout})
        if exc:
            raise exc
        return response
    monkeypatch.setattr(aduannomor.httpx, "post", post)


# --------------------------------------------------------------------------- the lookup itself

def test_request_shape_and_parse(monkeypatch, live_mode):
    calls = []
    _fake_post(monkeypatch, httpx.Response(200, json=_body(12, [{"jenis": "Penipuan", "jumlah": 9}])), calls=calls)
    out = aduannomor.check_phone("+62 857-1122-3344")
    assert out == {"reported_count": 12, "kejahatan": [{"name": "Penipuan", "count": 9}], "source": "live"}
    c = calls[0]
    assert c["url"] == "https://aduannomor.id/api/v.1/ticket/phone-check"
    assert c["data"] == {"calling_code": "+62", "phone_number": "85711223344"}  # no leading 0
    assert c["headers"]["Origin"] == "https://aduannomor.id" and "cek-nomor-seluler" in c["headers"]["Referer"]
    assert c["timeout"] == 3.0
    assert "reported_phone_status" not in out  # placeholder on their side: ignored


def test_cached_per_number(monkeypatch, live_mode):
    calls = []
    _fake_post(monkeypatch, httpx.Response(200, json=_body(3)), calls=calls)
    aduannomor.check_phone("0857-1122-3344")
    aduannomor.check_phone("085711223344")
    assert len(calls) == 1


@pytest.mark.parametrize("response,exc", [
    (httpx.Response(403, text="Forbidden"), None),
    (None, httpx.ConnectTimeout("timeout")),
    (httpx.Response(200, json={"weird": True}), None),
])
def test_failures_are_silent(monkeypatch, live_mode, response, exc):
    _fake_post(monkeypatch, response=response, exc=exc)
    assert aduannomor.check_phone("0857-1122-3344") is None


def test_fixture_used_when_live_fails(monkeypatch, live_mode):
    live_mode.write_text(json.dumps({"+6285711223344": _body(7)}))
    _fake_post(monkeypatch, response=httpx.Response(403))
    assert aduannomor.check_phone("0857-1122-3344") == {"reported_count": 7, "kejahatan": [], "source": "fixture"}


def test_off_mode_never_calls(monkeypatch):
    monkeypatch.setenv("ADUANNOMOR_MODE", "off")
    _fake_post(monkeypatch, exc=AssertionError("must not be called"))
    assert aduannomor.check_phone("0857-1122-3344") is None


def test_bundled_fixtures_are_real_captures():
    data = json.loads(aduannomor.FIXTURES.read_text())
    for key, body in data.items():
        if key.startswith("+62"):
            assert body["_captured"] and aduannomor.parse(body) is not None


# --------------------------------------------------------------------------- in the reply

def _fake_model(monkeypatch, **kw):
    base = dict(risk="BAHAYA", category="hadiah_palsu", confidence=0.9, reason="Minta bayar pajak hadiah dulu.",
                next_steps=["Jangan bayar apa pun.", "Abaikan pesannya.", "Blokir nomornya."],
                alert_guardian=True, reply_text="Ini penipuan.")
    base.update(kw)

    class P:
        name, model, supports_images = "meta", "muse-spark-1.3", False

        def chat(self, messages, tools=None, response_format=None, **kw):
            msg = SimpleNamespace(content=json.dumps(base), tool_calls=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    monkeypatch.setattr(orchestrator, "get_provider", lambda name=None: P())
    monkeypatch.setattr(orchestrator, "fallback_name", lambda primary: None)


def test_reported_count_line_written_by_code(monkeypatch, live_mode):
    _fake_model(monkeypatch)
    _fake_post(monkeypatch, httpx.Response(200, json=_body(12)))
    r = orchestrator.analyze(Input(type="text", text="Menang undian! WA admin 0857-1122-3344"))
    out = format_reply(r.verdict, footer=tools_footer(r))
    assert "ℹ️ Nomor ini sudah dilaporkan 12x di AduanNomor.id (Komdigi)." in out
    assert "Laporin nomornya di aduannomor.id ya" in out and r.verdict.scam_phone_numbers == ["0857-1122-3344"]
    assert "cek AduanNomor" in out and orchestrator.details(r)["aduannomor"]["0857-1122-3344"]["reported_count"] == 12


@pytest.mark.parametrize("response,exc", [(httpx.Response(200, json=_body(0)), None), (None, httpx.ReadTimeout("t"))])
def test_no_line_when_zero_or_failed(monkeypatch, live_mode, response, exc):
    _fake_model(monkeypatch)
    _fake_post(monkeypatch, response=response, exc=exc)
    r = orchestrator.analyze(Input(type="text", text="Menang undian! WA admin 0857-1122-3344"))
    out = format_reply(r.verdict)
    assert "AduanNomor.id (Komdigi)" not in out and "dilaporkan" not in out
    assert "Laporin nomornya di aduannomor.id ya" in out  # the fallback step is always there


def test_aman_never_looks_up(monkeypatch, live_mode):
    _fake_model(monkeypatch, risk="AMAN", alert_guardian=False, confidence=0.9)
    _fake_post(monkeypatch, exc=AssertionError("must not be called"))
    r = orchestrator.analyze(Input(type="text", text="Saya driver, nomor saya 0857-1122-3344"))
    assert r.verdict.lookups == []


def test_account_gets_cekrekening_step(monkeypatch):
    _fake_model(monkeypatch, category="belanja_online")
    r = orchestrator.analyze(Input(type="text", text="DP 500rb ke rek BRI 0123456789 a.n. Siska ya kak"))
    step = r.verdict.next_steps[-1]
    assert step == ("Cek dulu rekening 0123456789 di https://cekrekening.id/cek-rekening sebelum transfer. "
                    "Kalau ini penipu, laporkan di https://cekrekening.id/laporkan-rekening")
    assert len(r.verdict.next_steps) <= 4
    assert "cekrekening.github.io" not in format_reply(r.verdict)
    assert set(contacts.SITES) >= {"https://cekrekening.id/cek-rekening", "https://cekrekening.id/laporkan-rekening"}


def test_phone_and_account_together_stay_within_4_steps(monkeypatch):
    _fake_model(monkeypatch, category="belanja_online")
    r = orchestrator.analyze(Input(type="text", text="Transfer DP ke BCA 1234567890 a.n. Siska. Info WA 0857-1122-3344"))
    assert len(r.verdict.next_steps) == 4
    assert r.verdict.next_steps[-2] == "Laporin nomornya di aduannomor.id ya" and r.verdict.scam_phone_numbers == ["0857-1122-3344"]
    assert r.verdict.next_steps[-1].startswith("Cek dulu rekening 1234567890")


def test_own_report_count_from_other_users(monkeypatch):
    _fake_model(monkeypatch)
    users = [repo.upsert_tg_user(990000 + i, f"U{i}")[0].id for i in range(3)]
    text = "Menang undian! WA admin 0857-9999-1212, transfer ke BCA 8800112233"
    for i, uid in enumerate(users):
        r = orchestrator.analyze(Input(type="text", text=text, user_id=uid))
        case_id = repo.create_case(uid, "tg", "text", text, channel_message_id=f"tg:lookup:{i}")
        repo.save_verdict(case_id, r)
    # the third user sees the two earlier, different users; not themselves
    assert "Nomor ini sudah 2x dilaporkan pengguna Ronda." in r.verdict.lookups
    assert "Rekening ini sudah 2x dilaporkan pengguna Ronda." in r.verdict.lookups
    # the same user again doesn't inflate the count
    r = orchestrator.analyze(Input(type="text", text=text, user_id=users[0]))
    assert "Nomor ini sudah 2x dilaporkan pengguna Ronda." in r.verdict.lookups


# --------------------------------------------------------------------------- live (one real call)

import os  # noqa: E402

live = pytest.mark.skipif(not os.environ.get("RUN_LIVE"), reason="set RUN_LIVE=1 to call AduanNomor.id once")


@live
def test_live_aduannomor(monkeypatch):
    monkeypatch.setenv("ADUANNOMOR_MODE", "live")
    aduannomor._cache.clear()
    out = aduannomor.check_phone("081234567890")
    assert out is not None and out["source"] == "live" and isinstance(out["reported_count"], int)
