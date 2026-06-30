"""Durable checkpoints = git commits of the model buffer (plan §0, §10).

Git is the store — no DB. Each checkpoint writes the buffer to ``model.py`` and
commits it; the timeline is ``git log``; rollback is ``git show <sha>:model.py``
back into the buffer. Undo/redo is separate (in-memory buffer history on the
Document); this is the *durable* layer that survives restarts and versions in git.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_WORKSPACE = Path(os.environ.get("CAD_WORKSPACE", Path(__file__).resolve().parents[1] / ".workspace"))
_MODEL_FILE = "model.py"


class GitStore:
    def __init__(self, path: Path = _WORKSPACE) -> None:
        self.path = Path(path)
        self.file = self.path / _MODEL_FILE
        self._repo: Any | None = None

    def _ensure(self) -> Any:
        from git import Actor, Repo

        if self._repo is not None:
            return self._repo
        self.path.mkdir(parents=True, exist_ok=True)
        git_dir = self.path / ".git"
        if git_dir.exists():
            self._repo = Repo(self.path)
        else:
            self._repo = Repo.init(self.path)
            with self._repo.config_writer() as cw:
                cw.set_value("user", "name", "cadcode")
                cw.set_value("user", "email", "cadcode@localhost")
        self._actor = Actor("cadcode", "cadcode@localhost")
        return self._repo

    def checkpoint(self, source: str, message: str = "checkpoint") -> dict[str, Any]:
        repo = self._ensure()
        self.file.write_text(source)
        repo.index.add([_MODEL_FILE])
        # Skip empty commits (nothing changed since the last checkpoint).
        if repo.head.is_valid() and not repo.index.diff(repo.head.commit):
            return self._head_entry()
        commit = repo.index.commit(message, author=self._actor, committer=self._actor)
        return self._entry(commit)

    def log(self, limit: int = 100) -> list[dict[str, Any]]:
        repo = self._ensure()
        if not repo.head.is_valid():
            return []
        return [self._entry(c) for c in repo.iter_commits(max_count=limit)]

    def source_at(self, sha: str) -> str:
        repo = self._ensure()
        return repo.git.show(f"{sha}:{_MODEL_FILE}")

    def _entry(self, commit: Any) -> dict[str, Any]:
        return {
            "sha": commit.hexsha,
            "short": commit.hexsha[:8],
            "message": commit.message.strip(),
            "time": int(commit.committed_date),
        }

    def _head_entry(self) -> dict[str, Any]:
        return self._entry(self._ensure().head.commit)
