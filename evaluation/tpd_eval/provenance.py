from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


def repository_state(root: str | Path) -> dict[str, Any]:
    root = Path(root)
    def git(*args: str) -> str:
        result = subprocess.run(("git", *args), cwd=root, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
        return result.stdout.strip()
    return {"source_commit": git("rev-parse", "HEAD") or "unknown",
            "dirty_worktree": bool(git("status", "--porcelain"))}
