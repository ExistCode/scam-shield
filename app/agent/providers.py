"""Provider switch: one OpenAI-compatible client, base URL per provider.

meta      -> Muse Spark via https://api.meta.ai/v1 (default)
wonder    -> MUSE_ESCALATION_MODEL (Muse Wonder), same endpoint; writes the final verdict when Spark is
             unsure or the input is a video
fireworks -> Llama via Fireworks (optional fallback)
"""
from dataclasses import dataclass
from functools import lru_cache

from openai import OpenAI

from app.config import settings


@dataclass(frozen=True)
class Provider:
    name: str
    model: str
    client: OpenAI
    supports_reasoning_effort: bool
    supports_images: bool
    reasoning_effort: str = ""

    def chat(self, messages: list, tools: list | None = None, response_format: dict | None = None):
        kwargs: dict = {"model": self.model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if response_format:
            kwargs["response_format"] = response_format
        if self.supports_reasoning_effort and self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort
        return self.client.chat.completions.create(**kwargs)


@lru_cache(maxsize=4)
def get_provider(name: str | None = None) -> Provider:
    name = (name or settings.llm_provider or "meta").lower()
    if name == "fireworks":
        if not settings.fireworks_api_key:
            raise RuntimeError("FIREWORKS_API_KEY is not set")
        return Provider(
            name="fireworks",
            model=settings.fireworks_model,
            client=OpenAI(api_key=settings.fireworks_api_key, base_url=settings.fireworks_base_url, timeout=40),
            supports_reasoning_effort=False,
            supports_images=False,
        )
    if not settings.meta_api_key:
        raise RuntimeError("META_API_KEY is not set")
    if name == "wonder":
        return Provider(
            name="wonder",
            model=settings.wonder_model,
            client=OpenAI(api_key=settings.meta_api_key, base_url=settings.meta_base_url,
                          timeout=settings.wonder_timeout, max_retries=0),
            supports_reasoning_effort=True,
            supports_images=True,
            reasoning_effort=settings.wonder_reasoning_effort,
        )
    return Provider(
        name="meta",
        model=settings.muse_model,
        client=OpenAI(api_key=settings.meta_api_key, base_url=settings.meta_base_url, timeout=40, max_retries=1),
        supports_reasoning_effort=True,
        supports_images=True,
        reasoning_effort=settings.muse_reasoning_effort,
    )


def fallback_name(primary: str) -> str | None:
    """Which provider to try if the primary errors out (only if configured)."""
    if primary == "meta" and settings.fireworks_api_key:
        return "fireworks"
    return None
