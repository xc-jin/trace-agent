from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DomainError(ValueError):
    pass


class RecoverableToolError(DomainError):
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
    depends_on: list[str] = Field(
        max_length=32,
        description="Task CONSTRAINT KEYS only, such as deployment or license; never claim IDs or source IDs. Eligibility/recommendation conclusions must include the relevant constraint keys.",
    )
    independence_reason: str = Field(
        default="", max_length=1000,
        description="Required when depends_on is empty: explain why this claim is independent of every current task constraint. Empty dependencies still trigger conservative review on changes.",
    )
    evidence: list[EvidencePin] = Field(default_factory=list, max_length=12)
    limitations: str = Field(min_length=1, max_length=2000)

    @field_validator("text", "limitations")
    @classmethod
    def nonblank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def explicit_dependencies(self):
        if not self.depends_on and not self.independence_reason.strip():
            raise ValueError("Empty depends_on requires an independence_reason; use task constraint keys for suitability conclusions")
        return self


class ClarificationQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question: str = Field(min_length=1, max_length=1000)
    reason: str = Field(min_length=1, max_length=1000)


class TaskAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: Literal["ready", "needs_clarification"]
    reason: str = Field(min_length=1, max_length=1000)
    questions: list[ClarificationQuestion] = Field(max_length=3)

    @model_validator(mode="after")
    def matching_questions(self):
        if (self.status == "needs_clarification") != bool(self.questions):
            raise ValueError("needs_clarification requires questions; ready requires an empty questions list")
        return self
