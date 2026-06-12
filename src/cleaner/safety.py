"""Meta-protection: refuse to clean this very repository.

The cleaner is itself a Python project. Pointing it at its own repo would
either fail noisily or, worse, succeed and rewrite the agent definitions
out from under the running orchestrator. We block that case here.

The check is intentionally conservative — it compares the URL's
``owner/repo`` tail against a static set of self-identifiers. Self-cleaning
in a fork is permitted (different ``owner``) because that's a legitimate
sandboxed test setup; the protection only covers the canonical origin.
"""

from __future__ import annotations

from urllib.parse import urlparse

SELF_REPO_TAILS = frozenset(
    {
        "hackathon-lamaison",
    }
)
"""Repository slugs that identify this project. Match is on the trailing
``<repo>`` of ``<owner>/<repo>`` (case-insensitive), excluding any ``.git``
suffix. Add new identifiers here if the project is renamed or forked into
the same operational role.
"""


class SelfCleaningRefused(RuntimeError):
    """Raised when the orchestrator is asked to clean its own repository."""


def assert_not_self(repo_url: str) -> None:
    """Refuse if ``repo_url`` points at the cleaner's own repository.

    Args:
        repo_url: A GitHub repository URL (``https://github.com/owner/repo``
            with or without ``.git``).

    Raises:
        SelfCleaningRefused: If the repository tail matches a known
            self-identifier.
        ValueError: If the URL cannot be parsed as a repository URL.
    """
    parsed = urlparse(repo_url)
    if not parsed.path:
        raise ValueError(f"not a repository URL: {repo_url!r}")
    tail = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    if tail.endswith(".git"):
        tail = tail[: -len(".git")]
    if tail.lower() in SELF_REPO_TAILS:
        raise SelfCleaningRefused(
            f"refusing to clean {repo_url!r}: this is the cleaner's own "
            "repository. Fork it first if you want to use it as a test "
            "target."
        )
