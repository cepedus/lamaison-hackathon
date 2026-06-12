"""Smoke tests for the self-cleaning protection.

The cleaner is itself a Python project; pointing it at its own repo would
either fail or, worse, succeed and rewrite the agents out from under the
running orchestrator. ``cleaner.safety.assert_not_self`` is the load-bearing
guard. These tests pin its behavior.
"""

from __future__ import annotations

import pytest

from cleaner.safety import SelfCleaningRefused, assert_not_self


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/anthropic-labs/hackathon-lamaison",
        "https://github.com/anthropic-labs/hackathon-lamaison.git",
        "https://github.com/some-fork-owner/HACKATHON-LAMAISON",
        "git@github.com:owner/hackathon-lamaison.git",
    ],
)
def test_refuses_self_regardless_of_owner_or_suffix(url: str) -> None:
    """The check is by repo *name*, not full URL — forks are also refused
    to prevent foot-guns where the cleaner targets a near-identical copy
    of itself.
    """
    with pytest.raises(SelfCleaningRefused):
        assert_not_self(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/owner/some-legacy-project",
        "https://github.com/anthropic-labs/another-project.git",
        "https://github.com/owner/hackathon-lamaison-tools",  # substring, not equal
    ],
)
def test_permits_other_repos(url: str) -> None:
    assert_not_self(url) is None  # returns None on success


def test_rejects_malformed_url() -> None:
    with pytest.raises(ValueError):
        assert_not_self("")


# --- extract_owner_repo --------------------------------------------------
#
# Used to build the GitHub REST URL for create_pull_request. Tiny but
# load-bearing — a parsing bug here either targets the wrong repo or
# fails the PR call outright.

from cleaner.session import extract_owner_repo  # noqa: E402


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/owner/repo", ("owner", "repo")),
        ("https://github.com/owner/repo.git", ("owner", "repo")),
        ("https://github.com/owner/repo/", ("owner", "repo")),
        ("https://github.com/owner/repo/tree/main", ("owner", "repo")),
    ],
)
def test_extract_owner_repo_accepts_common_shapes(url: str, expected: tuple[str, str]) -> None:
    assert extract_owner_repo(url) == expected


@pytest.mark.parametrize("url", ["", "https://github.com/", "https://github.com/owner"])
def test_extract_owner_repo_rejects_malformed(url: str) -> None:
    with pytest.raises(ValueError):
        extract_owner_repo(url)
