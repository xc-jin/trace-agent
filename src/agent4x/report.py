from __future__ import annotations

from agent4x.store import Store


def render_report(store: Store, task_id: str) -> str:
    task = store.task(task_id)
    lines = [
        f"# {task['goal']}", "", f"项目：{task['project']} · 任务：{task['id']} · 约束版本：{task['contract_version']}",
        "", "> current 仅表示通过引用定位并按当前声明的依赖维护；不是事实已独立验证。",
        "", "## 当前任务约束", "",
    ]
    lines += [f"- `{key}`：{value}" for key, value in task["constraints"].items()] or ["尚未明确约束。"]
    questions = [question for question in task["questions"] if question["contract_version"] == task["contract_version"]]
    if questions:
        lines += ["", "## 待澄清问题", ""]
        for question in questions:
            lines += [f"- {question['question']}（原因：{question['reason']}）"]
    lines += ["", "## 结论与依据", ""]
    sources = {source["id"]: source for source in task["sources"]}
    if not task["claims"]:
        lines += ["暂无已登记结论。请先导入资料或运行 Agent。"]
    for claim in task["claims"]:
        badge = "需复核，请勿沿用" if claim["status"] == "needs_review" else "当前记录"
        lines += [
            f"### [{badge}] {claim['id']} · 修订 {claim['revision']}", "",
            f"类型：{claim['kind']} · 依赖：{', '.join(claim['depends_on']) or '未声明（保守复核）'}", "",
            claim["text"], "", f"局限：{claim['limitations']}", "",
        ]
        if claim["status"] == "needs_review":
            lines += [f"**复核原因：{claim['review_reason']}。以下内容是历史结论，不是当前推荐。**", ""]
        for pin in claim["evidence"]:
            source = sources[pin["source_id"]]
            lines += [
                f"- 来源 `{source['id']}`：{source['title']}（{source['kind']}）",
                f"  位置：{source['origin']} · 字符 [{pin['start']}, {pin['end']}) · SHA-256 `{pin['source_sha256']}`",
                "", *[f"> {line}" for line in pin["quote"].splitlines()], "",
            ]
        if len(claim["history"]) > 1:
            lines += ["修订历史：", ""]
            for version in claim["history"]:
                lines += [f"- r{version['revision']} / 约束 v{version['contract_version']}：{version['text']}"]
            lines += [""]
    lines += ["## 约束变更", ""]
    for change in task["changes"]:
        lines += [f"- v{change['contract_version']} `{change['key']}`：{change['before']} → {change['after']}；需复核：{', '.join(change['affected']) or '无'}"]
    if not task["changes"]:
        lines += ["暂无。"]
    lines += ["", "## 使用边界", "", "引用只校验文本存在及位置，尚不自动证明逻辑蕴含、事实真实性或来源权威性。", ""]
    return "\n".join(lines)
