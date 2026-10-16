from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

from langchain_core.tools import tool
from pydantic import Field, create_model

from agent4x.domain import ClaimDraft, RecoverableToolError, TaskAssessment, json_text, require_text
from agent4x.store import Store


def build_tools(store: Store, task_id: str, expected_version: int, search_client=None, assessment_id: str | None = None):
    task = store.task(task_id)
    constraint_keys = tuple(task["constraints"])
    review_ids = frozenset(claim["id"] for claim in task["claims"] if claim["status"] == "needs_review")
    preserved_ids = frozenset(claim["id"] for claim in task["claims"]) - review_ids
    dependency_type = list[Literal[constraint_keys]] if constraint_keys else list[str]
    bound_draft = create_model(
        "TaskClaimDraft", __base__=ClaimDraft,
        depends_on=(dependency_type, Field(
            max_length=32 if constraint_keys else 0,
            description=ClaimDraft.model_fields["depends_on"].description,
        )),
    )
    claim_input = create_model("SaveClaimInput", draft=(bound_draft, ...), claim_id=(str, ""))

    def feedback(message, code="invalid_tool_input"):
        return {"ok": False, "error": {"code": code, "message": message, "retryable": True}, "allowed_constraint_keys": list(constraint_keys)}

    def recover(action, *arguments):
        try:
            return action(*arguments)
        except RecoverableToolError as error:
            return feedback(str(error))

    def validation_feedback(error):
        issues = [
            {"field": ".".join(str(part) for part in item["loc"]), "message": item["msg"]}
            for item in error.errors()
        ]
        return json_text(feedback(issues, "invalid_arguments"))

    @tool
    def assess_task(assessment: TaskAssessment) -> dict:
        """FIRST step: assess whether user requirements are sufficient. Choose ready with no questions, or needs_clarification with concrete questions; the latter ends this run for user input."""
        return recover(store.assess, task_id, assessment, expected_version, assessment_id)

    @tool
    def get_task() -> dict:
        """Read the task contract, claims, pending questions and confirmed project memories."""
        return store.snapshot(task_id)

    @tool
    def search_sources(query: str) -> list[dict]:
        """Search only the sources already imported into this task; returns source IDs and excerpts."""
        return store.search(task_id, query)

    @tool
    def read_source(source_id: str, start: int = 0, limit: int = 6000) -> dict:
        """Read a bounded slice of one source in this task. Use exact quotes, not invented citations."""
        return recover(store.read_source, task_id, source_id, start, limit)

    @tool(args_schema=claim_input)
    def save_claim(draft: ClaimDraft, claim_id: str = "") -> dict:
        """Register/revise a claim. depends_on contains task CONSTRAINT KEYS, never claim IDs. Suitability against deployment/license depends on those keys. An empty list requires independence_reason and still causes conservative review. For revisions pass the original claim_id. Correct retryable errors before claiming success."""
        if review_ids and claim_id in preserved_ids:
            return feedback("This revision run preserves unaffected claims. Leave this claim unchanged; update only the original needs_review IDs or add genuinely new findings.", "outside_review_batch")
        return recover(store.save_claim, task_id, draft, expected_version, claim_id)

    @tool
    def request_clarification(question: str, reason: str) -> dict:
        """Record a necessary clarification and then explain it to the user. Does not change the task contract."""
        return store.question(task_id, question, reason, expected_version)

    @tool
    def propose_memory(text: str) -> dict:
        """Propose project-scoped experience for explicit human confirmation. Cannot activate or edit memories."""
        return store.propose_memory(task_id, text)

    tools = [assess_task, get_task, search_sources, read_source, save_claim, request_clarification, propose_memory]
    if search_client is not None:
        @tool
        def search_web(query: str) -> list[dict]:
            """Search Tavily only when the user enabled it. Store snippets, not independently fetched full webpages."""
            query = require_text(query, "query", 300)
            response = search_client.search(query=query, max_results=3, search_depth="basic")
            results = []
            for result in response.get("results", [])[:3]:
                address = result.get("url", "")
                parsed = urlsplit(address)
                if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                    continue
                content = result.get("content", "")
                if not isinstance(content, str) or not content.strip():
                    continue
                saved = store.add_source(
                    task_id, str(result.get("title", address))[:500], address,
                    content[:65000], "tavily_snippet",
                )
                results.append({**saved, "url": address, "excerpt": content[:2000], "kind": "tavily_snippet"})
            return results

        tools.append(search_web)
    for registered_tool in tools:
        registered_tool.handle_validation_error = validation_feedback
    return tools
