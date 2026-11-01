"""Media fetcher: media id -> URL -> bytes (Graph API, bearer token)."""
import httpx

from app.config import settings

MAX_MEDIA_BYTES = 16 * 1024 * 1024


def fetch_media(media_id: str) -> tuple[bytes, str]:
    headers = {"Authorization": f"Bearer {settings.wa_access_token}"}
    with httpx.Client(timeout=30, headers=headers) as client:
        meta = client.get(f"{settings.graph_base}/{media_id}")
        meta.raise_for_status()
        info = meta.json()
        if int(info.get("file_size") or 0) > MAX_MEDIA_BYTES:
            raise ValueError("media too large")
        r = client.get(info["url"])
        r.raise_for_status()
        return r.content, info.get("mime_type") or r.headers.get("content-type", "")
