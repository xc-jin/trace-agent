from __future__ import annotations

from pathlib import Path

from agent4x.domain import DomainError
from agent4x.store import Store


def import_source(store: Store, task_id: str, root: Path, relative: str) -> dict:
    root = root.expanduser().resolve(strict=True)
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts or any(part.startswith(".") for part in candidate.parts):
        raise DomainError("Use a non-hidden relative source path without parent traversal")
    path = (root / candidate).resolve(strict=True)
    if not path.is_relative_to(root) or path.suffix.lower() not in {".md", ".txt"} or not path.is_file():
        raise DomainError("Only .md/.txt files inside the selected source root are allowed")
    if any(part.startswith(".") for part in path.relative_to(root).parts):
        raise DomainError("Symlinks must not expose hidden source paths")
    if path.stat().st_size > 65000:
        raise DomainError("Source file exceeds 65000 bytes; split it into smaller documents")
    content = path.read_text(encoding="utf-8")
    return store.add_source(task_id, candidate.name, candidate.as_posix(), content, "local_text")
