"""Reads configuration from environment variables (Railway/Render env vars in prod, .env locally)."""
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _database_url() -> str:
    url = _env("DATABASE_URL", "sqlite:///./cekdulu.db")
    # Render gives postgres://...; SQLAlchemy + psycopg3 wants postgresql+psycopg://
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


@dataclass(frozen=True)
class Settings:
    # Models
    meta_api_key: str = field(default_factory=lambda: _env("META_API_KEY"))
    meta_base_url: str = field(default_factory=lambda: _env("META_API_BASE_URL", "https://api.meta.ai/v1"))
    muse_model: str = field(default_factory=lambda: _env("MUSE_MODEL", "muse-spark-1.3"))
    # Muse Spark does not accept reasoning_effort="none"
    muse_reasoning_effort: str = field(default_factory=lambda: _env("MUSE_REASONING_EFFORT", "low"))
    asr_model: str = field(default_factory=lambda: _env("ASR_MODEL", "muse-voice-transcribe-1.0"))
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "meta").lower())
    # Escalation: after Muse Spark answers, re-run the final verdict with this model (same tool results)
    # when confidence < MUSE_ESCALATION_THRESHOLD or the input is a video. Exact id from dev.meta.ai/docs/models.
    # Empty = off.
    wonder_model: str = field(default_factory=lambda: _env("MUSE_ESCALATION_MODEL", "muse-wonder-1.0"))
    wonder_reasoning_effort: str = field(default_factory=lambda: _env("MUSE_ESCALATION_REASONING_EFFORT", "low"))
    wonder_threshold: float = field(default_factory=lambda: float(_env("MUSE_ESCALATION_THRESHOLD", "0.6")))
    wonder_timeout: float = field(default_factory=lambda: float(_env("MUSE_ESCALATION_TIMEOUT", "30")))
    # Videos already spend time on ffmpeg + Muse Voice, so Wonder gets less time there.
    wonder_video_timeout: float = field(default_factory=lambda: float(_env("MUSE_ESCALATION_VIDEO_TIMEOUT", "12")))
    # Hard limit for one check in the chat channels; after this the user gets a WASPADA "hati-hati dulu" reply.
    check_deadline: float = field(default_factory=lambda: float(_env("CHECK_DEADLINE_SECONDS", "30")))
    fireworks_api_key: str = field(default_factory=lambda: _env("FIREWORKS_API_KEY"))
    fireworks_base_url: str = field(
        default_factory=lambda: _env("FIREWORKS_BASE_URL", "https://api.fireworks.ai/inference/v1")
    )
    fireworks_model: str = field(
        default_factory=lambda: _env("FIREWORKS_MODEL", "accounts/fireworks/models/llama4-maverick-instruct-basic")
    )

    # WhatsApp
    wa_access_token: str = field(default_factory=lambda: _env("WA_ACCESS_TOKEN"))
    wa_phone_number_id: str = field(default_factory=lambda: _env("WA_PHONE_NUMBER_ID"))
    wa_waba_id: str = field(default_factory=lambda: _env("WA_WABA_ID"))
    wa_verify_token: str = field(default_factory=lambda: _env("WA_VERIFY_TOKEN"))
    wa_app_secret: str = field(default_factory=lambda: _env("WA_APP_SECRET"))
    wa_graph_version: str = field(default_factory=lambda: _env("WA_GRAPH_VERSION", "v21.0"))
    wa_guardian_template: str = field(default_factory=lambda: _env("WA_GUARDIAN_TEMPLATE", "guardian_alert"))
    wa_template_lang: str = field(default_factory=lambda: _env("WA_TEMPLATE_LANG", "id"))

    # Telegram
    telegram_bot_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    # Sent by Telegram in X-Telegram-Bot-Api-Secret-Token; you choose it (A-Z a-z 0-9 _ -)
    telegram_webhook_secret: str = field(default_factory=lambda: _env("TELEGRAM_WEBHOOK_SECRET"))
    # If set (e.g. https://<app>.up.railway.app/telegram/webhook) the webhook is registered on startup
    telegram_webhook_url: str = field(default_factory=lambda: _env("TELEGRAM_WEBHOOK_URL"))

    # Data / misc
    database_url: str = field(default_factory=_database_url)
    safe_browsing_key: str = field(default_factory=lambda: _env("GOOGLE_SAFE_BROWSING_KEY"))
    max_tool_steps: int = field(default_factory=lambda: int(_env("MAX_TOOL_STEPS", "4")))

    # Security. On Railway/Render (or APP_ENV=production) missing secrets fail closed instead of
    # being skipped with a warning.
    production: bool = field(default_factory=lambda: _env("APP_ENV").lower() == "production"
                             or any(_env(v) for v in ("RAILWAY_ENVIRONMENT_ID", "RAILWAY_ENVIRONMENT_NAME",
                                                      "RAILWAY_ENVIRONMENT", "RENDER")))
    # Shared secret for POST /analyze (sent as X-Analyze-Token). Unset in production = endpoint off.
    analyze_token: str = field(default_factory=lambda: _env("ANALYZE_TOKEN"))
    analyze_rate_per_min: int = field(default_factory=lambda: int(_env("ANALYZE_RATE_PER_MIN", "10")))
    # Key for the /demo chat page. Entered once, then kept in an HttpOnly cookie that /analyze accepts.
    # Unset in production = /demo is off.
    demo_key: str = field(default_factory=lambda: _env("DEMO_KEY"))

    @property
    def graph_base(self) -> str:
        return f"https://graph.facebook.com/{self.wa_graph_version}"


settings = Settings()
