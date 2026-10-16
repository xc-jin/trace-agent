from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import ValidationError

from agent4x.agent import ScriptedModel, live_model, run_agent
from agent4x.demo import call
from agent4x.domain import ClaimDraft, DomainError, TaskAssessment
from agent4x.report import render_report
from agent4x.settings import Settings
from agent4x.store import Store
from agent4x.tools import build_tools


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Store(Path(self.temporary.name))
        self.task_id = self.store.new_task("选择部署方案", "workflow", {"deployment": "允许托管", "license": "宽松"})["id"]
        self.source_id = self.store.add_source(self.task_id, "Fixture", "fixture.md", "Alpha仅支持托管。Beta支持自托管。", "demo_fixture")["source_id"]
        self.ready = {"assessment": {"status": "ready", "reason": "用户约束已明确", "questions": []}}
        self.question = {"question": "公司是否要求数据不出内网？", "reason": "缺少会改变部署选择的用户要求"}
        self.waiting = {"assessment": {"status": "needs_clarification", "reason": "安全制度未知", "questions": [self.question]}}
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for target in ("socket.socket.connect", "socket.socket.connect_ex", "socket.getaddrinfo"):
            self.stack.enter_context(patch(target, side_effect=RuntimeError("Tests forbid external network")))

    def draft(self, **updates):
        result = {
            "text": "允许托管时可考虑Alpha", "kind": "inference", "depends_on": ["deployment"],
            "evidence": [{"source_id": self.source_id, "quote": "Alpha仅支持托管。"}], "limitations": "固定虚构资料",
        }
        result.update(updates)
        return result

    def test_revision_batch_preserves_unaffected_claim_after_review_completes(self):
        deployment = self.store.save_claim(self.task_id, ClaimDraft(**self.draft()), 1)
        license_draft = self.draft(text="许可证适配结论", depends_on=["license"])
        license_claim = self.store.save_claim(self.task_id, ClaimDraft(**license_draft), 1)
        self.store.amend(self.task_id, "deployment", "必须自托管")
        tools = {registered.name: registered for registered in build_tools(self.store, self.task_id, 2)}
        revised = tools["save_claim"].invoke({"draft": self.draft(text="托管方案不适用"), "claim_id": deployment["claim_id"]})
        self.assertEqual(revised["revision"], 2)
        blocked = tools["save_claim"].invoke({"draft": license_draft, "claim_id": license_claim["claim_id"]})
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["error"]["code"], "outside_review_batch")
        preserved = next(claim for claim in self.store.task(self.task_id)["claims"] if claim["id"] == license_claim["claim_id"])
        self.assertEqual(preserved["revision"], 1)
        fresh_tools = {registered.name: registered for registered in build_tools(self.store, self.task_id, 2)}
        self.assertEqual(fresh_tools["save_claim"].invoke({"draft": license_draft, "claim_id": license_claim["claim_id"]})["revision"], 2)

    def test_planning_tool_is_not_in_model_menu(self):
        model = ScriptedModel(responses=[call("assess_task", self.ready, "assessment"), AIMessage(content="完成")])
        run_agent(self.store, self.task_id, model, offline=True)
        names = {registered.name for registered in model.requests[1]["tools"]}
        self.assertNotIn("write_todos", names)
        self.assertIn("save_claim", names)

    def test_dependencies_required_and_empty_list_needs_explanation(self):
        missing = self.draft()
        del missing["depends_on"]
        with self.assertRaises(ValidationError):
            ClaimDraft.model_validate(missing)
        with self.assertRaises(ValidationError):
            ClaimDraft.model_validate(self.draft(depends_on=[]))
        valid = ClaimDraft.model_validate(self.draft(depends_on=[], independence_reason="仅描述资料，不评判任务适配"))
        self.assertEqual(valid.depends_on, [])

    def test_dependency_schema_enumerates_only_constraint_keys(self):
        tools = {entry.name: entry for entry in build_tools(self.store, self.task_id, 1)}
        schema = tools["save_claim"].args_schema.model_json_schema()
        definition = schema["$defs"]["TaskClaimDraft"]
        self.assertIn("depends_on", definition["required"])
        self.assertEqual(definition["properties"]["depends_on"]["items"]["enum"], ["deployment", "license"])

    def test_empty_contract_allows_only_explicit_independent_claims(self):
        task_id = self.store.new_task("只读资料", "empty", {})["id"]
        tools = {entry.name: entry for entry in build_tools(self.store, task_id, 1)}
        schema = tools["save_claim"].args_schema.model_json_schema()
        self.assertEqual(schema["$defs"]["TaskClaimDraft"]["properties"]["depends_on"]["maxItems"], 0)
        result = tools["save_claim"].invoke({"draft": {
            "text": "资料不足", "kind": "unresolved", "depends_on": [],
            "independence_reason": "当前没有任务约束", "limitations": "尚无来源",
        }})
        self.assertEqual(result["revision"], 1)

    def test_bad_dependency_returns_feedback_and_model_can_correct(self):
        model = ScriptedModel(responses=[
            call("assess_task", self.ready, "assess"),
            call("save_claim", {"draft": self.draft(depends_on=["claim_not_a_constraint"])}, "bad"),
            call("save_claim", {"draft": self.draft()}, "fixed"),
            AIMessage(content="已按约束键保存"),
        ])
        result = run_agent(self.store, self.task_id, model, offline=True)
        self.assertEqual(result["model_calls"], 4)
        feedback = model.requests[2]["messages"][-1]
        self.assertIsInstance(feedback, ToolMessage)
        self.assertEqual(feedback.status, "error")
        parsed = json.loads(feedback.content)
        self.assertFalse(parsed["ok"])
        self.assertTrue(parsed["error"]["retryable"])
        self.assertEqual(parsed["allowed_constraint_keys"], ["deployment", "license"])
        claims = self.store.task(self.task_id)["claims"]
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["depends_on"], ["deployment"])

    def test_invented_quote_returns_feedback_without_partial_write(self):
        model = ScriptedModel(responses=[
            call("assess_task", self.ready, "assess"),
            call("save_claim", {"draft": self.draft(evidence=[{"source_id": self.source_id, "quote": "编造的原文"}])}, "bad"),
            call("save_claim", {"draft": self.draft()}, "fixed"),
            AIMessage(content="已修正引用"),
        ])
        run_agent(self.store, self.task_id, model, offline=True)
        feedback = json.loads(model.requests[2]["messages"][-1].content)
        self.assertFalse(feedback["ok"])
        self.assertEqual(feedback["error"]["code"], "invalid_tool_input")
        self.assertEqual(len(self.store.task(self.task_id)["claims"]), 1)
        saved = [event for event in self.store.events(self.task_id) if event["kind"] == "claim.saved"]
        self.assertEqual(len(saved), 1)

    def test_empty_dependency_feedback_recovered_in_same_graph(self):
        model = ScriptedModel(responses=[
            call("assess_task", self.ready, "assess"),
            call("save_claim", {"draft": self.draft(depends_on=[])}, "bad"),
            call("save_claim", {"draft": self.draft()}, "fixed"),
            AIMessage(content="已保存"),
        ])
        run_agent(self.store, self.task_id, model, offline=True)
        self.assertFalse(json.loads(model.requests[2]["messages"][-1].content)["ok"])
        self.assertEqual(self.store.task(self.task_id)["claims"][0]["depends_on"], ["deployment"])

    def test_unknown_source_and_claim_are_retryable(self):
        tools = {entry.name: entry for entry in build_tools(self.store, self.task_id, 1)}
        self.assertFalse(tools["read_source"].invoke({"source_id": "missing"})["ok"])
        self.assertFalse(tools["save_claim"].invoke({"draft": self.draft(), "claim_id": "missing"})["ok"])
        self.assertEqual(self.store.task(self.task_id)["claims"], [])

    def test_stale_contract_is_not_converted_to_retryable_feedback(self):
        tools = {entry.name: entry for entry in build_tools(self.store, self.task_id, 1)}
        self.store.amend(self.task_id, "deployment", "必须自托管")
        with self.assertRaises(DomainError):
            tools["save_claim"].invoke({"draft": self.draft()})
        with self.assertRaises(DomainError):
            tools["assess_task"].invoke(self.ready)

    def test_other_domain_failures_are_not_swallowed(self):
        tools = {entry.name: entry for entry in build_tools(self.store, self.task_id, 1)}
        with patch.object(self.store, "save_claim", side_effect=DomainError("resource limit")), self.assertRaises(DomainError):
            tools["save_claim"].invoke({"draft": self.draft()})

    def test_initial_clarification_ends_without_another_model_call(self):
        model = ScriptedModel(responses=[call("assess_task", self.waiting, "assess")])
        result = run_agent(self.store, self.task_id, model, offline=True)
        self.assertEqual(result["status"], "needs_clarification")
        self.assertEqual(result["model_calls"], 1)
        self.assertEqual(result["tool_calls"], 1)
        self.assertIn(self.question["question"], result["answer"])
        task = self.store.task(self.task_id)
        self.assertEqual(task["readiness"]["status"], "needs_clarification")
        self.assertEqual(len(task["questions"]), 1)
        self.assertEqual(task["claims"], [])
        self.assertIn("不是完整最终建议", render_report(self.store, self.task_id))

    def test_waiting_task_rerun_does_not_spend_model_calls(self):
        self.store.assess(self.task_id, TaskAssessment.model_validate(self.waiting["assessment"]), 1)
        model = ScriptedModel(responses=[])
        result = run_agent(self.store, self.task_id, model, offline=True)
        self.assertEqual(result["status"], "needs_clarification")
        self.assertEqual(result["model_calls"], 0)
        self.assertEqual(len(self.store.task(self.task_id)["questions"]), 1)

    def test_user_amendment_resets_readiness_and_allows_new_run(self):
        self.store.assess(self.task_id, TaskAssessment.model_validate(self.waiting["assessment"]), 1)
        self.store.amend(self.task_id, "deployment", "必须自托管")
        self.assertEqual(self.store.task(self.task_id)["readiness"]["status"], "unassessed")
        model = ScriptedModel(responses=[call("assess_task", self.ready, "assess"), AIMessage(content="要求已明确")])
        result = run_agent(self.store, self.task_id, model, offline=True)
        self.assertEqual(result["status"], "stopped_unverified")
        self.assertNotIn("待澄清问题", render_report(self.store, self.task_id))

    def test_later_clarification_also_ends_before_next_model_call(self):
        model = ScriptedModel(responses=[
            call("assess_task", self.ready, "assess"),
            call("request_clarification", self.question, "question"),
        ])
        result = run_agent(self.store, self.task_id, model, offline=True)
        self.assertEqual(result["model_calls"], 2)
        self.assertEqual(result["status"], "needs_clarification")
        self.assertEqual(len(self.store.task(self.task_id)["questions"]), 1)
        tools = {entry.name: entry for entry in build_tools(self.store, self.task_id, 1)}
        self.assertFalse(tools["save_claim"].invoke({"draft": self.draft()})["ok"])

    def test_workflow_blocks_actions_before_assessment(self):
        model = ScriptedModel(responses=[
            call("propose_memory", {"text": "不应写入"}, "blocked"),
            call("assess_task", self.ready, "assess"),
            AIMessage(content="结束"),
        ])
        run_agent(self.store, self.task_id, model, offline=True)
        self.assertEqual(json.loads(model.requests[1]["messages"][-1].content)["error"]["code"], "workflow_gate")
        self.assertEqual(self.store.memories("workflow"), [])

    def test_unassessed_final_answer_is_rejected(self):
        model = ScriptedModel(responses=[AIMessage(content="跳过评估直接推荐")])
        with self.assertRaises(DomainError):
            run_agent(self.store, self.task_id, model, offline=True)
        self.assertEqual(self.store.events(self.task_id)[-1]["kind"], "run.failed")

    def test_invalid_assessment_can_be_corrected(self):
        model = ScriptedModel(responses=[
            call("assess_task", {"assessment": {"status": "needs_clarification", "reason": "未知", "questions": []}}, "bad"),
            call("assess_task", self.waiting, "fixed"),
        ])
        result = run_agent(self.store, self.task_id, model, offline=True)
        self.assertEqual(result["model_calls"], 2)
        self.assertEqual(result["status"], "needs_clarification")
        self.assertFalse(json.loads(model.requests[1]["messages"][-1].content)["ok"])
        self.assertEqual(len(self.store.task(self.task_id)["questions"]), 1)

    def test_prior_ready_assessment_does_not_bypass_new_run_gate(self):
        self.store.assess(self.task_id, TaskAssessment.model_validate(self.ready["assessment"]), 1)
        model = ScriptedModel(responses=[call("assess_task", self.ready, "assess"), AIMessage(content="结束")])
        run_agent(self.store, self.task_id, model, offline=True)
        self.assertEqual({entry.name for entry in model.requests[0]["tools"]}, {"assess_task"})

    def test_ready_cannot_clear_pending_questions_without_user_amendment(self):
        self.store.assess(self.task_id, TaskAssessment.model_validate(self.waiting["assessment"]), 1)
        tools = {entry.name: entry for entry in build_tools(self.store, self.task_id, 1)}
        self.assertFalse(tools["assess_task"].invoke(self.ready)["ok"])
        self.assertEqual(self.store.task(self.task_id)["readiness"]["status"], "needs_clarification")

    def test_assessment_questions_are_atomic_when_limit_exceeded(self):
        for sequence in range(20):
            self.store.question(self.task_id, f"问题{sequence}", "原因", 1)
        self.store.amend(self.task_id, "deployment", "新要求")
        with self.assertRaises(DomainError):
            self.store.assess(self.task_id, TaskAssessment.model_validate(self.waiting["assessment"]), 2)
        task = self.store.task(self.task_id)
        self.assertEqual(len(task["questions"]), 20)
        self.assertEqual(task["readiness"]["status"], "unassessed")

    def test_real_http_protocol_forces_assessment_and_ends_for_questions(self):
        requests = []

        def respond(request, **kwargs):
            payload = json.loads(request.content)
            requests.append(payload)
            self.assertEqual([entry["name"] for entry in payload["tools"]], ["assess_task"])
            self.assertEqual(payload["tool_choice"]["name"], "assess_task")
            return httpx.Response(200, request=request, json={
                "id": "msg_fixture", "type": "message", "role": "assistant", "model": "deepseek-flash",
                "content": [{"type": "tool_use", "id": "assessment", "name": "assess_task", "input": self.waiting}],
                "stop_reason": "tool_use", "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1},
            })

        model = live_model(Settings("fake-only", "deepseek-flash", "https://api.deepseek.com/anthropic"))
        with patch("httpx.Client.send", side_effect=respond):
            result = run_agent(self.store, self.task_id, model)
        self.assertEqual(len(requests), 1)
        self.assertEqual(result["status"], "needs_clarification")


if __name__ == "__main__":
    unittest.main()
