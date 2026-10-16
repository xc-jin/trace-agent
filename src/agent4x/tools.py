from __future__ import annotations

from urllib.parse import urlsplit

from langchain_core.tools import tool

from agent4x.domain import ClaimDraft, DomainError, require_text
from agent4x.store import Store


def build_tools(store: Store, task_id: str, expected_version: int, search_client=None):
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
        return store.read_source(task_id, source_id, start, limit)

    @tool
    def save_claim(draft: ClaimDraft, claim_id: str = "") -> dict:
        """Register an evidence-backed claim, or revise an existing claim by ID. Declare all relevant constraint keys."""
        return store.save_claim(task_id, draft, expected_version, claim_id)

    @tool
    def request_clarification(question: str, reason: str) -> dict:
        """Record a necessary clarification and then explain it to the user. Does not change the task contract."""
        return store.question(task_id, question, reason, expected_version)

    @tool
    def propose_memory(text: str) -> dict:
        """Propose project-scoped experience for explicit human confirmation. Cannot activate or edit memories."""
        return store.propose_memory(task_id, text)

    tools = [get_task, search_sources, read_source, save_claim, request_clarification, propose_memory]
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
    return tools
