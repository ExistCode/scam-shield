"""transcribe_audio: Muse Voice Transcribe (POST /v1/asr/transcribe).

Per https://dev.meta.ai/docs/speech-to-text: multipart/form-data with two parts,
`request` (JSON settings, application/json) and `audio` (WAV: mono, 16-bit PCM, 16 or 24 kHz).
Response: {"sessionId", "transcript", "audioDurationMs", "turns": []}. Max 10 min / 32 MB.
"""
import json
import subprocess
import tempfile

import httpx

from app.config import settings


def _ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


MAX_SECONDS = 300  # API allows 10 min; scam pitches are short and this keeps latency down


def to_wav(media_bytes: bytes) -> bytes:
    """Convert voice notes (ogg/opus), audio files or VIDEOS (mp4: audio track only) to 16 kHz mono WAV.

    Input goes through a temp file, not a pipe: mp4 often has its index at the end and needs seeking.
    """
    with tempfile.NamedTemporaryFile(suffix=".media") as src:
        src.write(media_bytes)
        src.flush()
        proc = subprocess.run(
            [_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-i", src.name, "-vn",
             "-t", str(MAX_SECONDS), "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-f", "wav", "pipe:1"],
            capture_output=True,
            timeout=60,
        )
    if proc.returncode != 0 or len(proc.stdout) <= 44:  # 44 = WAV header only, i.e. no audio track
        raise RuntimeError(f"ffmpeg failed or no audio: {proc.stderr.decode(errors='ignore')[:200]}")
    return proc.stdout


def transcribe_audio(audio_bytes: bytes, mime: str | None = None) -> dict:
    """Returns {"transcript": str, "duration_ms": int | None}."""
    if not settings.meta_api_key:
        raise RuntimeError("META_API_KEY is not set")
    wav = to_wav(audio_bytes)  # always normalize: the API only takes mono 16-bit PCM at 16/24 kHz
    request = {
        "model": settings.asr_model,
        "audioEncoding": "WAV",
        "languageBias": ["Indonesian"],  # a hint, not forced: mixed Javanese/English still works
        # Bias recognition toward words that matter for scam detection
        "keywords": ["OTP", "PIN", "QRIS", "BCA", "BRI", "Mandiri", "BNI", "PPATK", "Polda", "transfer", "rekening"],
    }
    resp = httpx.post(
        f"{settings.meta_base_url.rstrip('/')}/asr/transcribe",
        headers={"Authorization": f"Bearer {settings.meta_api_key}", "Accept": "application/json"},
        files={
            "request": (None, json.dumps(request), "application/json"),
            "audio": ("voice.wav", wav, "audio/wav"),
        },
        timeout=60,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"transcribe failed {resp.status_code}: {resp.text[:300]}")
    body = resp.json()
    text = body.get("transcript") or body.get("text") or ""
    if not text and isinstance(body.get("turns"), list):
        text = " ".join(t.get("transcript", "") for t in body["turns"])
    return {"transcript": text.strip(), "duration_ms": body.get("audioDurationMs")}
