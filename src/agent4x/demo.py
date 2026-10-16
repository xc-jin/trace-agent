from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage

from agent4x.agent import ScriptedModel, run_agent
from agent4x.domain import identifier
from agent4x.report import render_report
from agent4x.store import Store


FIXTURE = "Alpha仅支持托管部署。Beta支持内网自托管部署。Alpha与Beta均使用示例宽松许可证。本文件为虚构演示资料，不涉及真实产品。"


def call(name: str, arguments: dict, call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": arguments, "id": call_id, "type": "tool_call"}])


def run_demo(store: Store) -> dict:
    with ExitStack() as stack:
        for target in ("socket.socket.connect", "socket.socket.connect_ex", "socket.getaddrinfo"):
            stack.enter_context(patch(target, side_effect=RuntimeError("Demo forbids external network")))
        task = store.new_task("根据虚构资料选择部署方案，并在要求变化后修订。", identifier("offline_demo"), {
            "deployment": "允许托管部署", "license": "接受示例宽松许可证",
        })
        task_id = task["id"]
        source = store.add_source(task_id, "虚构方案资料", "demo://fictional-options", FIXTURE, "demo_fixture")
        deployment = {
            "text": "允许托管时，可以考虑Alpha；Beta是可自托管的备选。", "kind": "inference",
            "depends_on": ["deployment"], "limitations": "仅依据虚构资料，未比较实际成本与性能。",
            "evidence": [{"source_id": source["source_id"], "quote": "Alpha仅支持托管部署。Beta支持内网自托管部署。"}],
        }
        license_claim = {
            "text": "两者符合本任务的示例许可证要求。", "kind": "inference", "depends_on": ["license"],
            "limitations": "示例条款，不代表真实法律判断。",
            "evidence": [{"source_id": source["source_id"], "quote": "Alpha与Beta均使用示例宽松许可证。"}],
        }
        initial_model = ScriptedModel(responses=[
            call("get_task", {}, "task-1"),
            call("read_source", {"source_id": source["source_id"]}, "source-1"),
            call("save_claim", {"draft": deployment}, "claim-1"),
            call("save_claim", {"draft": license_claim}, "claim-2"),
            call("propose_memory", {"text": "本演示项目选择方案时需要明确部署边界。"}, "memory-1"),
            AIMessage(content="已登记两条基于虚构资料的结论。经验仍待用户确认。"),
        ])
        initial = run_agent(store, task_id, initial_model, offline=True)
        directory = store.directory / "demo" / task_id
        directory.mkdir(parents=True, mode=0o700)
        (directory / "01-initial.md").write_text(render_report(store, task_id))
        claims = store.task(task_id)["claims"]
        affected_id, unaffected_id = claims[0]["id"], claims[1]["id"]
        change = store.amend(task_id, "deployment", "数据不能离开内网，必须支持自托管")
        assert change["affected"] == [affected_id]
        (directory / "02-needs-review.md").write_text(render_report(store, task_id))
        revised = {**deployment, "text": "内网约束下撤回对Alpha的推荐，改为建议支持自托管的Beta。"}
        revision_model = ScriptedModel(responses=[
            call("get_task", {}, "task-2"),
            call("read_source", {"source_id": source["source_id"]}, "source-2"),
            call("save_claim", {"draft": revised, "claim_id": affected_id}, "revise-1"),
            AIMessage(content="已修订部署建议；许可证结论未被改写。"),
        ])
        revision = run_agent(store, task_id, revision_model, offline=True)
        (directory / "03-revised.md").write_text(render_report(store, task_id))
        result = store.task(task_id)
        assert result["claims"][0]["revision"] == 2
        assert result["claims"][1]["revision"] == 1
        assert store.memories(task["project"], active_only=True) == []
    return {
        "mode": "offline-scripted", "task_id": task_id, "reports": str(directory),
        "project": task["project"],
        "affected_claim": affected_id, "unchanged_claim": unaffected_id,
        "initial_model_calls": initial["model_calls"], "revision_model_calls": revision["model_calls"],
        "pending_memories": [entry["id"] for entry in store.memories(task["project"])],
        "claim": "Control-flow demonstration only; no real model or live search, no quality benchmark.",
    }
