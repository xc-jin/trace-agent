from __future__ import annotations

from contextlib import ExitStack
import json
from unittest.mock import patch

from langchain_core.messages import AIMessage

from agent4x.agent import ScriptedModel, run_agent
from agent4x.demo import call
from agent4x.domain import DomainError, identifier
from agent4x.report import render_report
from agent4x.review import build_review, render_review
from agent4x.scenarios import SCENARIO_NAME, create_scenario
from agent4x.store import Store


def run_knowledge_base_demo(store: Store) -> dict:
    with ExitStack() as stack:
        for target in ("socket.socket.connect", "socket.socket.connect_ex", "socket.getaddrinfo"):
            stack.enter_context(patch(target, side_effect=RuntimeError("Demo forbids external network")))
        setup = create_scenario(store, SCENARIO_NAME, identifier("knowledge_base_demo"), example_materials=True)
        task_id = setup["task_id"]
        source_id = setup["sources"][0]["source_id"]
        directory = store.directory / "demo" / task_id
        directory.mkdir(parents=True, mode=0o700)

        def save(name, content):
            destination = directory / name
            destination.write_text(content, encoding="utf-8")
            destination.chmod(0o600)

        question = {"question": "公司是否允许外部托管，还是必须内网自托管？", "reason": "该条件会改变候选软件的部署适配结论。"}
        clarification = run_agent(store, task_id, ScriptedModel(responses=[
            call("assess_task", {"assessment": {"status": "needs_clarification", "reason": "部署政策未明确", "questions": [question]}}, "assess-missing"),
        ]), offline=True)
        waiting_task = store.task(task_id)
        save("01-clarification.md", render_report(store, task_id))
        waiting = run_agent(store, task_id, ScriptedModel(responses=[]), offline=True)
        store.amend(task_id, "deployment", "允许外部托管，也可选择内网自托管")
        deployment = {
            "text": "允许托管时，可将AlphaKB作为托管候选；BetaKB提供内网自托管选项。资料不足以判定哪种方式成本更低。",
            "kind": "inference", "depends_on": ["deployment"],
            "evidence": [{"source_id": source_id, "quote": "AlphaKB仅提供外部托管的知识库检索服务，不支持内网自托管。"}, {"source_id": source_id, "quote": "BetaKB支持内网自托管的知识库检索，资料未说明是否提供托管服务。"}],
            "limitations": "仅依据虚构资料；没有部署验证、质量评测或安全认证，不推断BetaKB具有托管能力。",
        }
        license_claim = {
            "text": "按用户给定的示例许可证接受范围，两者在该维度均可进入候选。",
            "kind": "inference", "depends_on": ["license"],
            "evidence": [{"source_id": source_id, "quote": "AlphaKB与BetaKB均使用示例宽松许可证。"}],
            "limitations": "虚构示例许可，不代表真实法律意见或完成公司许可审查。",
        }
        unknown = {
            "text": "成本、运维投入、检索质量和安全审计结果仍待补充，当前只能作部署条件下的候选建议。",
            "kind": "unresolved", "depends_on": ["deployment"],
            "evidence": [{"source_id": source_id, "quote": "资料未提供费用、硬件需求、运维工时、检索准确率或独立安全审计结果。"}],
            "limitations": "不能由部署方式推算费用、检索质量或公司安全审查结果。",
        }
        ready = {"assessment": {"status": "ready", "reason": "用户已明确部署和示例许可条件，可给出有局限的候选建议", "questions": []}}
        initial = run_agent(store, task_id, ScriptedModel(responses=[
            call("assess_task", ready, "assess-initial"),
            call("read_source", {"source_id": source_id}, "read-initial"),
            call("save_claim", {"draft": deployment}, "deployment-initial"),
            call("save_claim", {"draft": license_claim}, "license-initial"),
            call("save_claim", {"draft": unknown}, "unknown-initial"),
            call("propose_memory", {"text": "本项目选型前应先向用户确认部署边界；不能从支持自托管推断支持托管。"}, "memory-initial"),
            AIMessage(content="已登记条件性建议和未知项；经验等待用户确认，尚未部署或批准选型。"),
        ]), offline=True)
        initial_task = store.task(task_id)
        save("02-initial.md", render_report(store, task_id))
        deployment_id, license_id, unknown_id = [claim["id"] for claim in initial_task["claims"]]
        change = store.amend(task_id, "deployment", "业务文档不能离开内网，必须支持内网自托管")
        save("03-needs-review.md", render_report(store, task_id))
        before_review = build_review(store, task_id)
        save("04-review-before.md", render_review(before_review))
        revised_deployment = {**deployment, "text": "部署条件已收紧：撤回AlphaKB托管候选建议；BetaKB可作为内网自托管候选，但尚未完成公司的安全审查。"}
        revised_unknown = {**unknown, "text": "内网自托管条件下，仍需补充BetaKB的硬件需求、运维投入、检索质量及安全审计资料；这些未知项没有因部署适配而解决。"}
        revision = run_agent(store, task_id, ScriptedModel(responses=[
            call("assess_task", ready, "assess-revision"),
            call("read_source", {"source_id": source_id}, "read-revision"),
            call("save_claim", {"draft": revised_deployment, "claim_id": deployment_id}, "deployment-revision"),
            call("save_claim", {"draft": revised_unknown, "claim_id": unknown_id}, "unknown-revision"),
            AIMessage(content="已按原ID修订部署建议和未知项，许可结论未改写。请人工复核依据及剩余风险。"),
        ]), offline=True)
        save("05-revised.md", render_report(store, task_id))
        after_review = build_review(store, task_id)
        save("06-review-after.md", render_review(after_review))
        final_claims = {claim["id"]: claim for claim in store.task(task_id)["claims"]}
        memories = store.memories(setup["project"])
        checks = {
            "clarification_before_claims": clarification["status"] == "needs_clarification" and bool(waiting_task["questions"]) and not waiting_task["claims"],
            "waiting_rerun_zero_model_calls": waiting["model_calls"] == 0,
            "selective_invalidation": set(change["affected"]) == {deployment_id, unknown_id},
            "old_advice_flagged_before_revision": before_review["summary"]["needs_review"] == 2,
            "original_id_and_history": final_claims[deployment_id]["revision"] == 2 and len(final_claims[deployment_id]["history"]) == 2,
            "license_preserved": final_claims[license_id]["revision"] == 1 and final_claims[license_id]["text"] == license_claim["text"],
            "unknowns_not_hidden": final_claims[unknown_id]["kind"] == "unresolved",
            "review_summary": after_review["summary"] == {"needs_review": 0, "revised": 2, "preserved": 1, "added": 0},
            "memory_requires_user_confirmation": bool(memories) and all(entry["status"] == "pending" for entry in memories) and not store.memories(setup["project"], True),
        }
        if not all(checks.values()):
            raise DomainError("Scenario demonstration failed its mechanical checks")
        save("07-trace.json", json.dumps(store.events(task_id), ensure_ascii=False, indent=2))
        result = {
            "mode": "offline-scripted", "scenario": SCENARIO_NAME, "task_id": task_id,
            "project": setup["project"], "reports": str(directory), "contract_version": 3,
            "model_calls": {"clarification": clarification["model_calls"], "waiting_rerun": waiting["model_calls"], "initial": initial["model_calls"], "revision": revision["model_calls"]},
            "checks": checks, "all_mechanical_checks_passed": True,
            "review_summary": after_review["summary"], "pending_memories": [entry["id"] for entry in memories],
            "claim": "Fictional fixtures and scripted model; zero external requests. Not a quality benchmark, legal review or security approval.",
        }
        save("08-summary.json", json.dumps(result, ensure_ascii=False, indent=2))
        return result
