import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.api.analyze import router as analyze_router
from app.channels.telegram_webhook import router as telegram_router
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
    if settings.telegram_bot_token and settings.telegram_webhook_url:
        from app.channels import telegram_bot

        try:
            telegram_bot.set_webhook(settings.telegram_webhook_url)
            logging.getLogger("cekdulu").info("telegram webhook registered")
        except Exception as exc:
            logging.getLogger("cekdulu").error("telegram setWebhook failed: %s", exc)
    yield


app = FastAPI(title="Cek Dulu", version="1.0", lifespan=lifespan)
app.include_router(webhook_router)
app.include_router(telegram_router)
app.include_router(analyze_router)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


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
    }
