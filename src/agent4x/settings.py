from __future__ import annotations

from dataclasses import dataclass, field
import os
from urllib.parse import urlsplit

from agent4x.domain import DomainError


@dataclass(frozen=True)
class Settings:
    api_key: str = field(repr=False)
    model: str
    base_url: str
    tavily_key: str = field(default="", repr=False)

    @classmethod
    def from_environment(cls):
        key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        model = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash").strip()
        base = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/anthropic").strip().rstrip("/")
        parsed = urlsplit(base)
        if not key:
            raise DomainError("Set DEEPSEEK_API_KEY in this project's .env or environment; never paste it into chat")
        if parsed.scheme != "https" or parsed.hostname != "api.deepseek.com" or parsed.path != "/anthropic" or parsed.query or parsed.fragment or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise DomainError("MVP only allows https://api.deepseek.com/anthropic")
        if not model or len(model) > 120 or ":" in model:
            raise DomainError("Invalid DeepSeek model identifier")
        return cls(key, model, base, os.environ.get("TAVILY_API_KEY", "").strip())
