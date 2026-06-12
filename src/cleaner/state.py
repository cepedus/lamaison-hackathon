"""Persistent state for a cleaning run.

A cleaning run is identified by the target repository URL. State carries the
IDs of the cloud resources provisioned for that run (environment, memory
store, per-phase agents) plus the index of the last successfully completed
phase. Persistence is a flat JSON file under ``.cleaner-state/`` keyed by a
slug derived from the URL.

The state is the single source of truth for resumability. ``setup`` is
idempotent against existing state: if an agent ID is already present, the
orchestrator reuses it instead of creating a duplicate.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlparse


STATE_DIR = Path(".cleaner-state")
"""Directory (relative to CWD) where run state files live. Created on first
write. Add to .gitignore — agent IDs are not secrets but they're noise.
"""


@dataclass
class RunState:
    """All state for one cleaning run.

    Attributes:
        repo_url: Canonical target repo URL (the run's primary key).
        environment_id: Managed Agents environment ID, shared across phases.
        memory_store_id: Memory store ID, shared across phases.
        agent_ids: Map of phase slug to agent ID. Populated by ``setup``;
            consumed by ``run``.
        last_completed_phase: Slug of the last phase whose grader returned
            ``satisfied``. ``None`` means no phase has completed yet.
        sessions: Map of phase slug to the most recent session ID for that
            phase. Useful for inspection and Console deep-linking.
    """

    repo_url: str
    environment_id: str | None = None
    memory_store_id: str | None = None
    agent_ids: dict[str, str] = field(default_factory=dict)
    last_completed_phase: str | None = None
    sessions: dict[str, str] = field(default_factory=dict)


def slug_for_url(repo_url: str) -> str:
    """Derive a filesystem-safe slug from a repo URL.

    ``https://github.com/owner/repo`` → ``github.com_owner_repo``. The slug
    is purely for human-friendly file naming; uniqueness is achieved by
    including the host and path.
    """
    parsed = urlparse(repo_url)
    raw = f"{parsed.netloc}{parsed.path}".rstrip("/")
    if raw.endswith(".git"):
        raw = raw[: -len(".git")]
    return re.sub(r"[^A-Za-z0-9._-]", "_", raw).strip("_")


def path_for(repo_url: str) -> Path:
    """Return the state file path for ``repo_url`` (not created yet)."""
    return STATE_DIR / f"{slug_for_url(repo_url)}.json"


def load(repo_url: str) -> RunState:
    """Load existing state for ``repo_url``, or return a fresh ``RunState``.

    A missing file is not an error — it means this is a new run.
    """
    path = path_for(repo_url)
    if not path.exists():
        return RunState(repo_url=repo_url)
    data = json.loads(path.read_text())
    return RunState(**data)


def save(state: RunState) -> None:
    """Atomically persist ``state`` to disk.

    Writes to a temp file in the same directory, then renames. This is the
    standard ``write-then-rename`` pattern for crash-safe state files: a
    concurrent reader either sees the old content or the new content,
    never a partial write.
    """
    STATE_DIR.mkdir(exist_ok=True)
    path = path_for(state.repo_url)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(state), indent=2, sort_keys=True))
    tmp.replace(path)
