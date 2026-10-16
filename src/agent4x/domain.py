from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DomainError(ValueError):
    pass


class BudgetExceeded(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def identifier(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def require_text(value: str, name: str, maximum: int = 8000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise DomainError(f"{name} must contain 1–{maximum} characters")
    return value.strip()


def constraint_key(value: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value):
        raise DomainError("constraint keys must match [a-z][a-z0-9_]{0,63}")
    return value


class EvidencePin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, max_length=80)
    quote: str = Field(min_length=1, max_length=2000)

    @field_validator("quote")
    @classmethod
    def nonblank_quote(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("quote must not be blank")
        return value


class ClaimDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=4000)
    kind: Literal["fact", "inference", "unresolved"]
    depends_on: list[str] = Field(default_factory=list, max_length=32)
    evidence: list[EvidencePin] = Field(default_factory=list, max_length=12)
    limitations: str = Field(min_length=1, max_length=2000)

    @field_validator("text", "limitations")
    @classmethod
    def nonblank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value.strip()
