from __future__ import annotations

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, ToolMessage

from agent4x.domain import DomainError, json_text
from agent4x.store import Store


class TaskWorkflow(AgentMiddleware):
    def __init__(self, store: Store, task_id: str, expected_version: int, assessment_id: str):
        self.store = store
        self.task_id = task_id
        self.expected_version = expected_version
        self.assessment_id = assessment_id

    def task(self):
        task = self.store.task(self.task_id)
        if task["contract_version"] != self.expected_version:
            raise DomainError("Contract changed during execution; start a fresh run")
        return task

    def assessed(self, task):
        readiness = task.get("readiness", {})
        return readiness.get("id") == self.assessment_id

    @hook_config(can_jump_to=["end"])
    def before_model(self, state, runtime):
        task = self.task()
        if task.get("readiness", {}).get("status") == "needs_clarification":
            questions = [entry for entry in task["questions"] if entry["contract_version"] == self.expected_version]
            answer = "任务需要你补充信息，暂不形成最终建议：\n" + "\n".join(
                f"- {entry['question']}（原因：{entry['reason']}）" for entry in questions
            )
            answer += "\n请通过 task amend 补充或修改约束后重新运行。"
            return {"messages": [AIMessage(content=answer)], "jump_to": "end"}
        return None

    def wrap_model_call(self, request, handler):
        pending = not self.assessed(self.task())
        tools = [
            registered for registered in request.tools
            if (getattr(registered, "name", None) == "assess_task") == pending
        ]
        return handler(request.override(tools=tools, tool_choice="assess_task" if pending else None))

    def wrap_tool_call(self, request, handler):
        task = self.task()
        name = request.tool_call["name"]
        waiting = task.get("readiness", {}).get("status") == "needs_clarification"
        if waiting or (not self.assessed(task) and name != "assess_task"):
            return ToolMessage(
                content=json_text({
                    "ok": False, "error": {
                        "code": "workflow_gate", "retryable": not waiting,
                        "message": "Await user clarification" if waiting else "Call assess_task before other tools",
                    },
                }),
                tool_call_id=request.tool_call["id"], name=name, status="error",
            )
        return handler(request)

    def after_model(self, state, runtime):
        message = state["messages"][-1]
        if not self.assessed(self.task()) and not getattr(message, "tool_calls", None):
            raise DomainError("Readiness assessment is required before a final answer")
        return None
