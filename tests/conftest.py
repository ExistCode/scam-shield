import os
import tempfile

# Isolated DB, and never talk to real WhatsApp/Telegram from tests.
# The Meta key is only kept for the live matrix (RUN_LIVE=1); offline tests must not spend credits.
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["WA_VERIFY_TOKEN"] = "test-verify"
os.environ["WA_APP_SECRET"] = "test-secret"
os.environ["TELEGRAM_WEBHOOK_SECRET"] = "tg-secret"
os.environ["TELEGRAM_WEBHOOK_URL"] = ""
os.environ["WA_ACCESS_TOKEN"] = ""
os.environ["WA_PHONE_NUMBER_ID"] = ""
os.environ["TELEGRAM_BOT_TOKEN"] = "123:test-token"
os.environ["GOOGLE_SAFE_BROWSING_KEY"] = ""
if not os.environ.get("RUN_LIVE"):
    os.environ["META_API_KEY"] = ""
    os.environ["FIREWORKS_API_KEY"] = ""

from app.db.session import init_db  # noqa: E402

init_db()
