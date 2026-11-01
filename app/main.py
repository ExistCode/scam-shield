import logging
from contextlib import asynccontextmanager
from pathlib import Path

import hmac

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from app.api.analyze import DEMO_COOKIE, demo_cookie_value, has_demo_access
from app.api.analyze import router as analyze_router
from app.channels.telegram_webhook import router as telegram_router
from app.channels.web_demo import router as demo_router
from app.channels.whatsapp_webhook import router as webhook_router
from app.config import settings
from app.db.session import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# httpx logs full request URLs at INFO; Telegram URLs contain the bot token.
logging.getLogger("httpx").setLevel(logging.WARNING)
STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    if settings.production and settings.telegram_webhook_url and not settings.telegram_webhook_secret:
        logging.getLogger("cekdulu").error("TELEGRAM_WEBHOOK_SECRET is required in production; webhook not registered")
    elif settings.telegram_bot_token and settings.telegram_webhook_url:
        from app.channels import telegram_bot

        try:
            telegram_bot.set_webhook(settings.telegram_webhook_url)
            logging.getLogger("cekdulu").info("telegram webhook registered")
        except Exception as exc:
            logging.getLogger("cekdulu").error("telegram setWebhook failed: %s", exc)
    yield


app = FastAPI(title="Ronda", version="1.0", lifespan=lifespan)
app.include_router(webhook_router)
app.include_router(telegram_router)
app.include_router(analyze_router)
app.include_router(demo_router)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


DEMO_LOGIN = """<!doctype html><html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Ronda demo</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#efeae2;
font:16px -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;color:#111b21}
form{background:#fff;padding:24px;border-radius:14px;width:min(340px,90vw);box-shadow:0 2px 10px rgba(0,0,0,.12)}
h1{font-size:18px;margin:0 0 4px;color:#008069}p{margin:0 0 16px;color:#667781;font-size:14px}
input{width:100%;box-sizing:border-box;padding:10px 12px;border:1px solid #d1d7db;border-radius:8px;font:inherit}
button{margin-top:12px;width:100%;padding:10px;border:0;border-radius:8px;background:#008069;color:#fff;font:inherit;cursor:pointer}
.err{color:#c0392b;font-size:14px;min-height:18px;margin-top:8px}</style></head><body>
<form id="f"><h1>Ronda demo</h1><p>Masukkan kunci demo.</p>
<input id="k" type="password" autocomplete="current-password" aria-label="Kunci demo" autofocus>
<button type="submit">Masuk</button><div class="err" id="e" role="alert"></div></form>
<script>document.getElementById("f").onsubmit=async(ev)=>{ev.preventDefault();
const r=await fetch("/demo/login",{method:"POST",headers:{"Content-Type":"application/json"},
body:JSON.stringify({key:document.getElementById("k").value})});
if(r.ok)location.reload();else document.getElementById("e").textContent="Kunci salah.";};</script></body></html>"""


class DemoLogin(BaseModel):
    key: str = Field(max_length=200)


@app.post("/demo/login", include_in_schema=False)
def demo_login(body: DemoLogin, request: Request):
    """Exchange DEMO_KEY for an HttpOnly cookie (the key never appears in URLs, logs, or page JS)."""
    if not settings.demo_key or not hmac.compare_digest(body.key, settings.demo_key):
        raise HTTPException(401, "wrong key")
    resp = JSONResponse({"ok": True})
    resp.set_cookie(DEMO_COOKIE, demo_cookie_value(), max_age=7 * 24 * 3600, httponly=True, samesite="strict",
                    secure=request.url.scheme == "https", path="/")
    return resp


@app.get("/demo", include_in_schema=False)
def demo_page(request: Request):
    """Mobile chat page on top of POST /analyze, protected by DEMO_KEY."""
    if not settings.demo_key:
        if settings.production:
            return HTMLResponse("Demo is off: set DEMO_KEY.", status_code=403)
    elif not has_demo_access(request):
        return HTMLResponse(DEMO_LOGIN, status_code=401, headers={"Cache-Control": "no-store"})
    return FileResponse(STATIC / "demo.html", headers={"Cache-Control": "no-store"})


@app.get("/wa", include_in_schema=False)
def wa_demo():
    """WhatsApp-style simulator (two phones) on top of the same backend, for screen recordings."""
    return FileResponse(STATIC / "wa.html", headers={"Cache-Control": "no-store"})


@app.get("/health")
def health():
    return {
        "ok": True,
        "provider": settings.llm_provider,
        "model": settings.muse_model if settings.llm_provider == "meta" else settings.fireworks_model,
        "meta_configured": bool(settings.meta_api_key),
        "whatsapp_configured": bool(settings.wa_access_token and settings.wa_phone_number_id),
        "signature_check": bool(settings.wa_app_secret),
        "telegram_configured": bool(settings.telegram_bot_token),
        "telegram_secret_check": bool(settings.telegram_webhook_secret),
        "analyze_locked": bool(settings.analyze_token) or settings.production,
    }
