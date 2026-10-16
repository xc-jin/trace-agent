from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from typing import Callable

from agent4x.domain import (
    ClaimDraft, DomainError, constraint_key, digest, identifier, json_text,
    require_text, utc_now,
)


class Store:
    def __init__(self, directory: Path):
        self.directory = directory.expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / "state.sqlite3"
        with self.connection() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, document TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS memories (id TEXT PRIMARY KEY, document TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT, created_at TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL
                );
            """)
        self.path.chmod(0o600)

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _read(connection: sqlite3.Connection, task_id: str) -> dict:
        row = connection.execute("SELECT document FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise DomainError(f"Unknown task: {task_id}")
        return json.loads(row["document"])

    @staticmethod
    def _event(connection: sqlite3.Connection, task_id: str | None, kind: str, payload: dict):
        connection.execute(
            "INSERT INTO events (task_id, created_at, kind, payload) VALUES (?, ?, ?, ?)",
            (task_id, utc_now(), kind, json_text(payload)),
        )

    def record(self, task_id: str | None, kind: str, payload: dict):
        with self.connection() as connection:
            self._event(connection, task_id, kind, payload)

    def _change(self, task_id: str, kind: str, action: Callable) -> dict:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = self._read(connection, task_id)
            result = action(task)
            task["updated_at"] = utc_now()
            connection.execute("UPDATE tasks SET document = ? WHERE id = ?", (json_text(task), task_id))
            self._event(connection, task_id, kind, result)
        return result

    def new_task(self, goal: str, project: str, constraints: dict[str, str]) -> dict:
        goal = require_text(goal, "goal", 4000)
        project = require_text(project, "project", 120)
        checked = {constraint_key(key): require_text(value, "constraint", 1000) for key, value in constraints.items()}
        task = {
            "id": identifier("task"), "project": project, "goal": goal,
            "contract_version": 1, "constraints": checked, "sources": [], "claims": [],
            "questions": [], "changes": [], "created_at": utc_now(), "updated_at": utc_now(),
        }
        with self.connection() as connection:
            connection.execute("INSERT INTO tasks VALUES (?, ?)", (task["id"], json_text(task)))
            self._event(connection, task["id"], "task.created", {"version": 1, "project": project})
        return task

    def task(self, task_id: str) -> dict:
        with self.connection() as connection:
            return self._read(connection, task_id)

    def snapshot(self, task_id: str) -> dict:
        task = self.task(task_id)
        task["sources"] = [
            {key: source[key] for key in ("id", "title", "origin", "kind", "sha256", "created_at")}
            for source in task["sources"]
        ]
        task["memories"] = self.memories(task["project"], active_only=True)
        for claim in task["claims"]:
            claim["history"] = [
                {"revision": version["revision"], "contract_version": version["contract_version"], "text": version["text"][:300]}
                for version in claim["history"][-2:]
            ]
        return task

    def amend(self, task_id: str, key: str, value: str) -> dict:
        key = constraint_key(key)
        value = require_text(value, "constraint", 1000)

        def action(task):
            previous = task["constraints"].get(key)
            if previous == value:
                return {"changed": False, "contract_version": task["contract_version"], "affected": []}
            added = key not in task["constraints"]
            task["constraints"][key] = value
            task["contract_version"] += 1
            affected = []
            for claim in task["claims"]:
                if added or not claim["depends_on"] or key in claim["depends_on"]:
                    claim["status"] = "needs_review"
                    claim["review_reason"] = f"Constraint {key} changed"
                    affected.append(claim["id"])
            change = {
                "changed": True, "key": key, "before": previous, "after": value,
                "contract_version": task["contract_version"], "affected": affected, "at": utc_now(),
            }
            task["changes"].append(change)
            return change

        return self._change(task_id, "contract.amended", action)

    def add_source(self, task_id: str, title: str, origin: str, content: str, kind: str) -> dict:
        title = require_text(title, "title", 500)
        origin = require_text(origin, "origin", 2000)
        content = require_text(content, "source content", 65000)
        if kind not in {"local_text", "tavily_snippet", "demo_fixture"}:
            raise DomainError("Unsupported source kind")
        source = {
            "id": identifier("source"), "title": title, "origin": origin,
            "content": content, "kind": kind, "sha256": digest(content), "created_at": utc_now(),
        }

        def action(task):
            existing = next((entry for entry in task["sources"] if entry["origin"] == origin and entry["sha256"] == source["sha256"]), None)
            if existing:
                return {"source_id": existing["id"], "duplicate": True}
            if len(task["sources"]) >= 40:
                raise DomainError("Source limit reached (40 per task)")
            task["sources"].append(source)
            return {"source_id": source["id"], "duplicate": False, "sha256": source["sha256"]}

        return self._change(task_id, "source.added", action)

    def read_source(self, task_id: str, source_id: str, start: int = 0, limit: int = 6000) -> dict:
        if type(start) is not int or start < 0 or type(limit) is not int or not 1 <= limit <= 10000:
            raise DomainError("Invalid source slice")
        source = next((source for source in self.task(task_id)["sources"] if source["id"] == source_id), None)
        if source is None:
            raise DomainError("Unknown source in this task")
        return {
            **{key: source[key] for key in ("id", "title", "origin", "kind", "sha256")},
            "start": start, "content": source["content"][start:start + limit], "total_characters": len(source["content"]),
        }

    def search(self, task_id: str, query: str) -> list[dict]:
        import re

        query = require_text(query, "query", 300).lower()
        terms = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", query)
        terms = [part for term in terms for part in ([term] if not re.search(r"[\u4e00-\u9fff]", term) else [term] + [term[index:index + 2] for index in range(len(term) - 1)])]
        results = []
        for source in self.task(task_id)["sources"]:
            lowered = source["content"].lower()
            matches = [lowered.find(term) for term in terms if term in lowered]
            if matches:
                start = max(0, min(matches) - 120)
                results.append({
                    "source_id": source["id"], "title": source["title"], "kind": source["kind"],
                    "score": len(matches), "start": start, "excerpt": source["content"][start:start + 2000],
                })
        return sorted(results, key=lambda result: result["score"], reverse=True)[:5]

    def save_claim(self, task_id: str, draft: ClaimDraft, expected_version: int, claim_id: str = "") -> dict:
        def action(task):
            if task["contract_version"] != expected_version:
                raise DomainError("Contract changed during this run; start a fresh run")
            if not set(draft.depends_on).issubset(task["constraints"]):
                raise DomainError("Claim refers to an unknown constraint key")
            if draft.kind != "unresolved" and not draft.evidence:
                raise DomainError("Facts and inferences require at least one evidence quote")
            checked = []
            for pin in draft.evidence:
                source = next((entry for entry in task["sources"] if entry["id"] == pin.source_id), None)
                if source is None or pin.quote not in source["content"]:
                    raise DomainError("Evidence must be an exact quote from a source in this task")
                start = source["content"].index(pin.quote)
                checked.append({**pin.model_dump(), "source_sha256": source["sha256"], "start": start, "end": start + len(pin.quote)})
            existing = next((entry for entry in task["claims"] if entry["id"] == claim_id), None) if claim_id else None
            if claim_id and existing is None:
                raise DomainError("Unknown claim in this task")
            if not existing and len(task["claims"]) >= 30:
                raise DomainError("Claim limit reached (30 per task)")
            version = {
                **draft.model_dump(), "evidence": checked, "contract_version": expected_version,
                "revision": len(existing["history"]) + 1 if existing else 1, "at": utc_now(),
            }
            if existing:
                history = existing["history"] + [version]
                existing.update({**version, "history": history, "status": "current", "review_reason": ""})
                saved = existing
            else:
                saved = {**version, "id": identifier("claim"), "status": "current", "review_reason": "", "history": [version]}
                task["claims"].append(saved)
            return {"claim_id": saved["id"], "revision": saved["revision"], "status": saved["status"]}

        return self._change(task_id, "claim.saved", action)

    def question(self, task_id: str, question: str, reason: str, expected_version: int) -> dict:
        question = require_text(question, "question", 1000)
        reason = require_text(reason, "reason", 1000)

        def action(task):
            if task["contract_version"] != expected_version:
                raise DomainError("Contract changed; start a fresh run")
            entry = {"id": identifier("question"), "question": question, "reason": reason, "contract_version": expected_version}
            if len(task["questions"]) >= 20:
                raise DomainError("Question limit reached")
            task["questions"].append(entry)
            return entry

        return self._change(task_id, "clarification.proposed", action)

    def propose_memory(self, task_id: str, text: str) -> dict:
        task = self.task(task_id)
        entry = {
            "id": identifier("memory"), "project": task["project"], "text": require_text(text, "memory", 1000),
            "status": "pending", "origin_task": task_id, "created_at": utc_now(),
        }
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            count = connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            if count >= 200:
                raise DomainError("Memory limit reached")
            connection.execute("INSERT INTO memories VALUES (?, ?)", (entry["id"], json_text(entry)))
            self._event(connection, task_id, "memory.proposed", {"memory_id": entry["id"], "project": entry["project"]})
        return entry

    def memories(self, project: str, active_only: bool = False) -> list[dict]:
        with self.connection() as connection:
            entries = [json.loads(row["document"]) for row in connection.execute("SELECT document FROM memories ORDER BY id")]
        return [entry for entry in entries if entry["project"] == project and (not active_only or entry["status"] == "active")]

    def memory_action(self, memory_id: str, action: str, text: str = "") -> dict:
        if action not in {"confirm", "delete", "edit"}:
            raise DomainError("Unknown memory action")
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT document FROM memories WHERE id = ?", (memory_id,)).fetchone()
            if row is None:
                raise DomainError("Unknown memory")
            entry = json.loads(row["document"])
            if action == "delete":
                connection.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            else:
                if action == "edit":
                    entry.update(text=require_text(text, "memory", 1000), status="pending")
                else:
                    entry["status"] = "active"
                connection.execute("UPDATE memories SET document = ? WHERE id = ?", (json_text(entry), memory_id))
            self._event(connection, entry["origin_task"], f"memory.{action}", {"memory_id": memory_id})
        return {"memory_id": memory_id, "action": action, "status": "deleted" if action == "delete" else entry["status"]}

    def events(self, task_id: str) -> list[dict]:
        with self.connection() as connection:
            rows = connection.execute("SELECT * FROM events WHERE task_id = ? ORDER BY sequence", (task_id,)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]
