"""/demo chat page: DEMO_KEY gate, HttpOnly cookie that /analyze accepts, details (trace) in the response."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main
from app.api import analyze as api
from app.agent.schema import FALLBACK_VERDICT, AnalysisResult


def _settings(**kw):
    base = dict(demo_key="", analyze_token="", production=False, analyze_rate_per_min=50)
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def client(monkeypatch):
    def install(**kw):
        s = _settings(**kw)
        monkeypatch.setattr(api, "settings", s)
        monkeypatch.setattr(main, "settings", SimpleNamespace(demo_key=s.demo_key, production=s.production))
        monkeypatch.setattr(api, "analyze", lambda inp: AnalysisResult(
            verdict=FALLBACK_VERDICT, provider="meta", model="muse-spark-1.3", tools_used=["check_link"],
            tool_outputs={"check_link": [{"url": "http://x.xyz", "domain": "x.xyz", "redirects": 0, "flags": ["domain_murahan"]}]}))
        api._hits.clear()
        return TestClient(main.app)
    return install


def test_page_needs_key_and_cookie_unlocks_analyze(client):
    c = client(demo_key="s3cret-demo", analyze_token="tok", production=True)
    r = c.get("/demo")
    assert r.status_code == 401 and "Cek Dulu demo" in r.text and "s3cret-demo" not in r.text
    assert c.post("/analyze", json={"type": "text", "text": "halo"}).status_code == 401  # no cookie, no token

    assert c.post("/demo/login", json={"key": "wrong"}).status_code == 401
    r = c.post("/demo/login", json={"key": "s3cret-demo"})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "s3cret-demo" not in cookie

    page = c.get("/demo")
    assert page.status_code == 200 and "Cek Dulu demo" in page.text and page.headers["cache-control"] == "no-store"
    assert "s3cret-demo" not in page.text  # the key is never in the page

    r = c.post("/analyze", json={"type": "text", "text": "halo"})  # cookie is enough
    assert r.status_code == 200
    d = r.json()["details"]
    assert d["trace"][0]["step"] == "check_link" and "domain_murahan" in d["trace"][0]["result"]


def test_forged_cookie_rejected(client):
    c = client(demo_key="s3cret-demo", analyze_token="tok", production=True)
    c.cookies.set("cekdulu_demo", "0" * 64)
    assert c.get("/demo").status_code == 401
    assert c.post("/analyze", json={"type": "text", "text": "halo"}).status_code == 401


def test_demo_off_in_production_without_key(client):
    c = client(production=True)
    assert c.get("/demo").status_code == 403
    assert c.post("/demo/login", json={"key": ""}).status_code == 401


def test_demo_open_locally_without_key(client):
    c = client()
    assert c.get("/demo").status_code == 200
