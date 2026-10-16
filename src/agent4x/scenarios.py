from __future__ import annotations

from importlib.resources import files

from agent4x.domain import DomainError
from agent4x.store import Store


SCENARIO_NAME = "knowledge-base"
GOAL = """为研发团队的内部知识库选择候选软件与部署方式，并维护随需求变化的决策记录。
先确认 deployment 和 license 中尚未确认的用户条件，不要自行假设公司安全制度或许可接受范围。
只依据导入资料，分别登记部署建议、按用户给定范围的许可证适配判断、真正未解决的成本/运维/检索质量问题。
每条建议写明适用条件、约束依赖、原文依据和局限；支持自托管不代表支持托管，不推断未记载的能力。
约束变化后沿用原 ID 复核受影响结论，明确撤回旧建议，保留未受影响记录。
不执行部署，不处理业务知识库正文，不作法律或安全合规认证。"""


def list_scenarios() -> list[dict]:
    return [{
        "name": SCENARIO_NAME,
        "description": "内部知识库软件选型：需求澄清、证据记录、约束变更与定向复核",
        "constraints": ["deployment", "license"],
        "example_materials": "显式开启才导入虚构资料，不涉及真实产品",
    }]


def create_scenario(store: Store, name: str, project: str, *, constraints: dict | None = None, example_materials: bool = False) -> dict:
    if name != SCENARIO_NAME:
        raise DomainError(f"Unknown scenario: {name}")
    initial = {
        "deployment": "尚未确认：公司是否允许外部托管，还是必须内网自托管？",
        "license": "接受示例宽松许可证" if example_materials else "尚未确认：请由用户明确许可证接受范围，不由模型解释或决定公司政策。",
    }
    initial.update(constraints or {})
    goal = GOAL + ("\n本任务仅使用虚构示例，不能用于真实产品选型。" if example_materials else "")
    task = store.new_task(goal, project, initial)
    sources = []
    if example_materials:
        material = files("agent4x").joinpath("assets", "knowledge-base", "candidates.md").read_text(encoding="utf-8")
        sources.append(store.add_source(task["id"], "内部知识库候选资料（虚构）", "scenario://knowledge-base/candidates.md", material, "demo_fixture"))
    return {
        "scenario": name, "task_id": task["id"], "project": project,
        "example_materials": example_materials, "constraints": initial, "sources": sources,
        "next_steps": [
            "确认 deployment 与 license；用 task amend 修改尚未确认的条件。",
            "真实选型请用 source add 导入公开或脱敏的 Markdown/TXT 产品资料；不要混用虚构资料。",
            "run 产生外部 API 请求；先检查发送的资料，再运行并人工阅读 report。",
            "已有约束改变后，先用 review 查看过期项，再 run 复核，最后 review 对照新旧结论。",
        ],
        "warning": "此模板不构成安全或法律认证；真实 run 会把上下文发送给外部模型。",
    }
