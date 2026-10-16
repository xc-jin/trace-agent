from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httpx
from langchain_core.messages import AIMessage, ToolMessage

from agent4x.agent import ScriptedModel, build_agent, live_model, run_agent
from agent4x.demo import call, run_demo
from agent4x.domain import BudgetExceeded, DomainError
from agent4x.runtime import AuditTrace, CallBudget
from agent4x.settings import Settings
from agent4x.store import Store
from agent4x.tools import build_tools


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Store(Path(self.temporary.name))
        self.task_id = self.store.new_task("了解当前任务", "test-project", {"deployment": "允许托管"})["id"]
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for target in ("socket.socket.connect", "socket.socket.connect_ex", "socket.getaddrinfo"):
            self.stack.enter_context(patch(target, side_effect=RuntimeError("Tests forbid external network")))

    def test_real_graph_tool_round_trip(self):
        model = ScriptedModel(responses=[call("assess_task", {"assessment": {"status": "ready", "reason": "要求明确", "questions": []}}, "task-1"), AIMessage(content="收到任务。")])
        result = run_agent(self.store, self.task_id, model, offline=True)
        self.assertEqual(result["model_calls"], 2)
        self.assertEqual(result["tool_calls"], 1)
        self.assertIsInstance(model.requests[1]["messages"][-1], ToolMessage)
        names = {tool.name if hasattr(tool, "name") else tool["name"] for tool in model.requests[0]["tools"]}
        self.assertEqual(names, {"assess_task"})
        later_names = {tool.name if hasattr(tool, "name") else tool["name"] for tool in model.requests[1]["tools"]}
        self.assertIn("get_task", later_names)
        self.assertTrue({"execute", "task", "read_file", "write_file"}.isdisjoint(names))
        self.assertEqual(self.store.events(self.task_id)[-1]["kind"], "run.ended")

    def test_graph_model_limit(self):
        model = ScriptedModel(responses=[call("assess_task", {"assessment": {"status": "ready", "reason": "要求明确", "questions": []}}, "task-1"), AIMessage(content="不得调用")])
        with self.assertRaises(BudgetExceeded):
            run_agent(self.store, self.task_id, model, model_limit=1, offline=True)
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(self.store.events(self.task_id)[-1]["kind"], "run.failed")

    def test_graph_tool_limit(self):
        model = ScriptedModel(responses=[call("assess_task", {"assessment": {"status": "ready", "reason": "要求明确", "questions": []}}, "first"), call("get_task", {}, "second")])
        with self.assertRaises(BudgetExceeded):
            run_agent(self.store, self.task_id, model, tool_limit=1, offline=True)

    def test_excluded_tool_cannot_execute(self):
        model = ScriptedModel(responses=[call("write_file", {"file_path": "/bad.md", "content": "bad"}, "blocked")])
        with self.assertRaises(BudgetExceeded):
            run_agent(self.store, self.task_id, model, offline=True)

    def test_memory_authority_not_exposed(self):
        tools = {tool.name for tool in build_tools(self.store, self.task_id, 1)}
        self.assertIn("propose_memory", tools)
        self.assertNotIn("confirm_memory", tools)
        self.assertNotIn("amend", tools)
        self.assertNotIn("search_web", tools)

    def test_complete_revision_demo(self):
        result = run_demo(self.store)
        task = self.store.task(result["task_id"])
        self.assertEqual(task["claims"][0]["revision"], 2)
        self.assertEqual(task["claims"][1]["revision"], 1)
        self.assertEqual(result["initial_model_calls"], 6)
        self.assertEqual(result["revision_model_calls"], 4)
        self.assertEqual(len(list(Path(result["reports"]).glob("*.md"))), 3)
        self.assertEqual(self.store.memories(result["project"], True), [])

    def test_demo_is_repeatable_after_memory_confirmation(self):
        first = run_demo(self.store)
        self.store.memory_action(first["pending_memories"][0], "confirm")
        second = run_demo(self.store)
        self.assertNotEqual(first["project"], second["project"])

    def test_search_uses_snippets_without_page_fetching(self):
        client = Mock()
        client.search.return_value = {"results": [
            {"url": "https://example.org/doc", "title": "Fixture", "content": "Fixture snippet"},
            {"url": "file:///private", "title": "Bad", "content": "not allowed"},
        ]}
        tools = {tool.name: tool for tool in build_tools(self.store, self.task_id, 1, client)}
        results = tools["search_web"].invoke({"query": "fixture"})
        self.assertEqual(len(results), 1)
        self.assertEqual(self.store.task(self.task_id)["sources"][0]["kind"], "tavily_snippet")

    def test_trace_masks_environment_credentials(self):
        trace = AuditTrace(self.store, self.task_id, "test-run")
        with patch.dict("os.environ", {"EXAMPLE_API_KEY": "fake-sensitive-value"}):
            trace.write("tool.end", "id", None, {"text": "fake-sensitive-value"})
        self.assertEqual(self.store.events(self.task_id)[-1]["payload"]["data"]["text"], "[REDACTED]")

    def test_budget_has_allowlist_and_context_boundary(self):
        budget = CallBudget(1, 1, 30, {"get_task"})
        with self.assertRaises(BudgetExceeded):
            budget.wrap_tool_call(SimpleNamespace(tool_call={"name": "execute"}), lambda request: None)
        with self.assertRaises(BudgetExceeded):
            budget.wrap_model_call(SimpleNamespace(messages=[AIMessage(content="a" * 80001)]), lambda request: None)

    def test_settings_and_live_model_build_do_not_call_network(self):
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "fake-only", "DEEPSEEK_MODEL": "deepseek-flash", "DEEPSEEK_BASE_URL": "https://api.deepseek.com/anthropic"}):
            settings = Settings.from_environment()
        model = live_model(settings)
        graph, budget, version = build_agent(self.store, self.task_id, model)
        self.assertEqual(version, 1)
        self.assertEqual(budget.model_calls, 0)
        self.assertIsNotNone(graph)

    def test_invalid_endpoint_rejected(self):
        for address in ("http://api.deepseek.com/anthropic", "https://evil.example/anthropic", "https://api.deepseek.com/anthropic?key=bad"):
            with self.subTest(address=address), patch.dict("os.environ", {"DEEPSEEK_API_KEY": "fake-only", "DEEPSEEK_BASE_URL": address}), self.assertRaises(DomainError):
                Settings.from_environment()

    def test_settings_repr_hides_credentials(self):
        settings = Settings("private-example-key", "deepseek-flash", "https://api.deepseek.com/anthropic", "private-search-key")
        self.assertNotIn("private-example-key", repr(settings))
        self.assertNotIn("private-search-key", repr(settings))

    def test_real_client_http_tool_result_round_trip(self):
        requests = []

        def respond(request, **kwargs):
            self.assertEqual(str(request.url), "https://api.deepseek.com/anthropic/v1/messages")
            self.assertEqual(request.headers.get("x-api-key"), "fake-deepseek-only")
            payload = json.loads(request.content)
            requests.append(payload)
            self.assertEqual(payload["thinking"], {"type": "disabled"})
            self.assertEqual(payload["model"], "deepseek-flash")
            content = [{"type": "tool_use", "id": "contract-1", "name": "get_task", "input": {}}] if len(requests) == 1 else [{"type": "text", "text": "收到资料"}]
            return httpx.Response(200, request=request, json={
                "id": "msg_fixture", "type": "message", "role": "assistant",
                "model": "deepseek-flash", "content": content,
                "stop_reason": "tool_use" if len(requests) == 1 else "end_turn",
                "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1},
            })

        model = live_model(Settings("fake-deepseek-only", "deepseek-flash", "https://api.deepseek.com/anthropic"))
        tool = build_tools(self.store, self.task_id, 1)[0]
        bound = model.bind_tools([tool])
        with patch("httpx.Client.send", side_effect=respond):
            response = bound.invoke("查询任务")
            self.assertEqual(response.tool_calls[0]["id"], "contract-1")
            result = ToolMessage(content="local fixture", tool_call_id="contract-1")
            final = bound.invoke([("human", "查询任务"), response, result])
        self.assertEqual(final.content, "收到资料")
        blocks = requests[1]["messages"][-1]["content"]
        self.assertTrue(any(block.get("tool_use_id") == "contract-1" for block in blocks))


if __name__ == "__main__":
    unittest.main()
