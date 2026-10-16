from __future__ import annotations

from agent4x.domain import DomainError
from agent4x.store import Store


LABELS = {"needs_review": "待复核", "revised": "已修订", "preserved": "保留未改写", "added": "新增"}
WARNING = "这是按契约版本整理的账本差异，不是人工批准、语义正确性证明或任意时间点的精确回放。依赖可能遗漏；同一契约版本内的多次修订不会拆成独立基线。"


def claim_view(claim: dict) -> dict:
    return {key: claim[key] for key in ("revision", "contract_version", "kind", "text", "depends_on", "limitations", "evidence")}


def build_review(store: Store, task_id: str, since_version: int | None = None) -> dict:
    task = store.task(task_id)
    current = task["contract_version"]
    baseline = current - 1 if since_version is None else since_version
    if type(baseline) is not int or not 1 <= baseline < current:
        raise DomainError("Review requires 1 <= since-version < current contract version; before any constraint change, use report")
    summary = {name: 0 for name in LABELS}
    entries = []
    for claim in task["claims"]:
        history = [version for version in claim["history"] if version["contract_version"] <= baseline]
        before = history[-1] if history else None
        if claim["status"] == "needs_review":
            change = "needs_review"
        elif before is None:
            change = "added"
        else:
            change = "preserved" if before["revision"] == claim["revision"] else "revised"
        summary[change] += 1
        entries.append({
            "claim_id": claim["id"], "change": change, "status": claim["status"],
            "review_reason": claim["review_reason"],
            "before": claim_view(before) if before else None, "after": claim_view(claim),
        })
    questions = [question for question in task["questions"] if question["contract_version"] == current]
    readiness = task.get("readiness", {}).get("status", "unassessed")
    if readiness == "needs_clarification":
        state = "needs_clarification"
    elif summary["needs_review"]:
        state = "needs_review"
    elif not entries:
        state = "no_claims"
    elif readiness != "ready":
        state = "unassessed"
    else:
        state = "recorded_unverified"
    return {
        "task_id": task_id, "goal": task["goal"], "since_version": baseline,
        "current_version": current, "state": state, "summary": summary,
        "constraint_changes": [change for change in task["changes"] if change["contract_version"] > baseline],
        "claims": entries, "questions": questions,
        "unresolved_claim_ids": [claim["id"] for claim in task["claims"] if claim["kind"] == "unresolved"],
        "sources": [{key: source[key] for key in ("id", "title", "origin", "kind", "sha256")} for source in task["sources"]],
        "warning": WARNING,
    }


def render_review(review: dict) -> str:
    states = {"needs_clarification": "等待用户澄清", "needs_review": "仍有旧结论待复核", "no_claims": "尚无已登记结论", "unassessed": "当前约束尚未完成评估", "recorded_unverified": "账本复核已记录，尚未经人工批准"}
    lines = [
        "# 技术选型变更对照", "", f"任务：{review['task_id']} · 契约 v{review['since_version']} → v{review['current_version']}",
        "", f"状态：{states[review['state']]}", "", f"> {review['warning']}", "", "## 约束变化", "",
    ]
    for change in review["constraint_changes"]:
        lines.append(f"- v{change['contract_version']} `{change['key']}`：{change['before']} → {change['after']}")
    sources = {source["id"]: source for source in review["sources"]}
    lines += ["", "## 结论变化", "", " | ".join(f"{label} {review['summary'][name]}" for name, label in LABELS.items()), "", f"仍有未解决项：{len(review['unresolved_claim_ids'])}。待复核清零不代表所有问题已解决。", ""]
    for entry in review["claims"]:
        before, after = entry["before"], entry["after"]
        lines += [f"### [{LABELS[entry['change']]}] {entry['claim_id']}", ""]
        if before:
            lines += [f"基线 r{before['revision']} / 契约 v{before['contract_version']}：{before['text']}", ""]
        prefix = "待复核的旧记录（不可沿用为当前建议）" if entry["change"] == "needs_review" else "当前记录"
        lines += [f"{prefix} r{after['revision']} / 契约 v{after['contract_version']}：{after['text']}", "", f"类型：{after['kind']} · 依赖：{', '.join(after['depends_on']) or '空依赖，保守复核'}", f"局限：{after['limitations']}", ""]
        if entry["review_reason"]:
            lines += [f"复核原因：{entry['review_reason']}", ""]
        for pin in after["evidence"]:
            source = sources[pin["source_id"]]
            lines += [f"- 来源 `{pin['source_id']}`：{source['title']}（{source['kind']}） · {source['origin']}", f"  字符 [{pin['start']}, {pin['end']}) · SHA-256 `{pin['source_sha256']}`", "", *[f"> {line}" for line in pin["quote"].splitlines()], ""]
    if review["questions"]:
        lines += ["## 待澄清", ""]
        lines += [f"- {question['question']}（{question['reason']}）" for question in review["questions"]]
    lines += ["", "## 人工交付检查", "", "- 核对原文是否真正支持建议，不只检查引文存在。", "- 检查依赖是否遗漏、是否仍有待复核或未解决事项。", "- 确认最新部署要求和许可接受范围；安全、法律结论交由相应人员确认。", ""]
    return "\n".join(lines)
