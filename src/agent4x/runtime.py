from __future__ import annotations

import json
import os
import threading
from time import monotonic

from langchain.agents.middleware import AgentMiddleware
from langchain_core.callbacks import BaseCallbackHandler

from agent4x.domain import BudgetExceeded
from agent4x.store import Store


class CallBudget(AgentMiddleware):
    def __init__(self, model_limit: int, tool_limit: int, seconds: float, allowed_tools: set[str]):
        if not 1 <= model_limit <= 20 or not 1 <= tool_limit <= 40 or not 1 <= seconds <= 600:
            raise ValueError("Limits: model calls 1–20, tool calls 1–40, seconds 1–600")
        self.model_limit = model_limit
        self.tool_limit = tool_limit
        self.model_calls = 0
        self.tool_calls = 0
        self.started = monotonic()
        self.seconds = seconds
        self.lock = threading.Lock()
        self.allowed_tools = allowed_tools

    def consume(self, kind: str):
        with self.lock:
            if monotonic() - self.started >= self.seconds:
                raise BudgetExceeded("Run time limit reached at an execution boundary")
            attribute = f"{kind}_calls"
            count = getattr(self, attribute)
            if count >= getattr(self, f"{kind}_limit"):
                raise BudgetExceeded(f"{kind} call limit reached")
            setattr(self, attribute, count + 1)

    def wrap_model_call(self, request, handler):
        if sum(len(json.dumps(message.content, ensure_ascii=False, default=str)) for message in request.messages) > 80000:
            raise BudgetExceeded("Context exceeds the 80000-character MVP boundary; split the task")
        self.consume("model")
        return handler(request)

    def wrap_tool_call(self, request, handler):
        self.consume("tool")
        if request.tool_call["name"] not in self.allowed_tools:
            raise BudgetExceeded("Tool is outside this run's allowlist")
        return handler(request)


class AuditTrace(BaseCallbackHandler):
    raise_error = True

    def __init__(self, store: Store, task_id: str, execution_id: str):
        self.store = store
        self.task_id = task_id
        self.execution_id = execution_id

    def write(self, kind: str, run_id, parent_run_id, payload):
        encoded = json.dumps(payload, ensure_ascii=False, default=str)
        for name, value in os.environ.items():
            if name.endswith(("API_KEY", "TOKEN", "SECRET")) and len(value) >= 4:
                encoded = encoded.replace(value, "[REDACTED]")
        safe = json.loads(encoded) if len(encoded) <= 100000 else {"truncated_preview": encoded[:100000]}
        self.store.record(self.task_id, kind, {
            "execution_id": self.execution_id, "run_id": str(run_id),
            "parent_run_id": str(parent_run_id) if parent_run_id else None, "data": safe,
        })

    def on_chat_model_start(self, serialized, messages, *, run_id, parent_run_id=None, **kwargs):
        self.write("model.start", run_id, parent_run_id, [[message.model_dump(mode="json") for message in batch] for batch in messages])

    def on_llm_end(self, response, *, run_id, parent_run_id=None, **kwargs):
        self.write("model.end", run_id, parent_run_id, [generation.message.model_dump(mode="json") for batch in response.generations for generation in batch if hasattr(generation, "message")])

    def on_tool_start(self, serialized, input_str, *, run_id, parent_run_id=None, **kwargs):
        self.write("tool.start", run_id, parent_run_id, {"name": serialized.get("name"), "input": input_str})

    def on_tool_end(self, output, *, run_id, parent_run_id=None, **kwargs):
        self.write("tool.end", run_id, parent_run_id, output.model_dump(mode="json") if hasattr(output, "model_dump") else str(output))

    def on_tool_error(self, error, *, run_id, parent_run_id=None, **kwargs):
        self.write("tool.error", run_id, parent_run_id, {"error_type": type(error).__name__})

    def on_llm_error(self, error, *, run_id, parent_run_id=None, **kwargs):
        self.write("model.error", run_id, parent_run_id, {"error_type": type(error).__name__})
