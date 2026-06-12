"""Drive one phase session from kickoff to terminal grader result.

This module owns the event-loop semantics that every phase share:

- **Stream-first ordering.** Open the SSE stream before sending the
  ``user.define_outcome`` kickoff. SSE has no replay, so events emitted
  before the stream opens are lost.

- **Correct idle-break gate.** A session transitions to ``status_idle``
  transiently between tool calls and while waiting on user input. Breaking
  on bare idle exits prematurely. The contract is: break on
  ``session.status_terminated``, *or* on ``session.status_idle`` whose
  ``stop_reason.type`` is *not* ``requires_action``.

- **Custom tool round-trip.** Phases declare two custom tools:
  ``ask_human`` for unsafe-to-decide-alone questions, and
  ``create_pull_request`` for opening PRs. Both follow the same pattern:
  the agent emits ``agent.custom_tool_use``, the orchestrator executes
  the side effect (prompting the human, or calling the GitHub REST API
  with the user's PAT), and replies with ``user.custom_tool_result``
  keyed by the event's ID. The PAT never enters the container — this is
  the host-side-credentials pattern that lets us avoid a Managed Agents
  vault entirely.

- **Terminal outcome tracking.** The grader emits
  ``span.outcome_evaluation_end`` with a ``result`` field. We record the
  most recent value; the orchestrator uses it to decide whether to advance.

The module intentionally does not handle stream reconnection. A dropped
stream surfaces as an SDK exception and aborts the phase — for the
hackathon timeframe, restart-by-resume is simpler than mid-flight recovery.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import IO, Any, Literal
from urllib.parse import urlparse

from anthropic import Anthropic

from cleaner.phases import Phase


OutcomeResult = Literal[
    "satisfied",
    "needs_revision",
    "max_iterations_reached",
    "failed",
    "interrupted",
    "terminated_without_outcome",
]


@dataclass(frozen=True, slots=True)
class PhaseRun:
    """The outcome of running one phase session.

    Attributes:
        session_id: The session that was created. Useful for Console
            deep-linking and debugging.
        result: Terminal grader result, or ``terminated_without_outcome`` if
            the session ended before any grader iteration finished.
        explanation: Grader's free-text explanation accompanying the
            terminal result. ``None`` if no outcome event fired.
        iterations: Number of grader iterations observed.
    """

    session_id: str
    result: OutcomeResult
    explanation: str | None
    iterations: int


HumanInput = Callable[[str, str], str]
"""Signature for human-checkpoint prompts: (question, context) -> answer.

Phases call ``ask_human`` when a decision should not be made autonomously.
The orchestrator supplies an implementation — the default reads from stdin,
but tests can inject a deterministic stub.
"""


def default_human_input(question: str, context: str) -> str:
    """Print the prompt to stderr and read one line from stdin.

    Stderr (not stdout) so the answer flow doesn't get confused with
    structured output piped from the CLI.
    """
    print("\n" + "─" * 60, file=sys.stderr)
    print("HUMAN CHECKPOINT", file=sys.stderr)
    print("─" * 60, file=sys.stderr)
    print(f"Q: {question}", file=sys.stderr)
    if context:
        print(f"\nContext:\n{context}", file=sys.stderr)
    print("─" * 60, file=sys.stderr)
    return input("Answer> ").strip() or "no preference; agent decides"


# --- Custom tools installed on every phase's agent --------------------
#
# Both tools follow the same shape: the agent calls them and the
# orchestrator handles the side effect host-side. Neither requires the
# credential to be exposed to the session container.

ASK_HUMAN_TOOL = {
    "type": "custom",
    "name": "ask_human",
    "description": (
        "Ask the human operator a question that should not be decided "
        "autonomously: load-bearing refactors, ambiguous bug-vs-feature "
        "calls, irreversible operations. Block on the response before "
        "proceeding. Use sparingly — frequent prompts defeat the point of "
        "an agent."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "One-line question requiring a decision.",
            },
            "context": {
                "type": "string",
                "description": (
                    "Details the human needs to answer: file:line "
                    "references, the relevant code snippet, the options "
                    "you are choosing between."
                ),
            },
        },
        "required": ["question", "context"],
        "additionalProperties": False,
    },
}


CREATE_PR_TOOL = {
    "type": "custom",
    "name": "create_pull_request",
    "description": (
        "Open a pull request on the target repository. Call this after "
        "pushing your branch with `git push`. The orchestrator "
        "authenticates with the user's GitHub PAT and the token never "
        "enters the container."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "PR title."},
            "body": {
                "type": "string",
                "description": "PR description in Markdown.",
            },
            "head_branch": {
                "type": "string",
                "description": ("Branch to merge from — typically cleaner/<NN>-<slug>."),
            },
            "base_branch": {
                "type": "string",
                "description": "Branch to merge into. Defaults to main.",
                "default": "main",
            },
        },
        "required": ["title", "body", "head_branch"],
        "additionalProperties": False,
    },
}


def extract_owner_repo(repo_url: str) -> tuple[str, str]:
    """Parse ``owner`` and ``repo`` out of a GitHub URL.

    Accepts ``https://github.com/owner/repo`` with or without a trailing
    ``.git`` and with arbitrary trailing path segments (which are
    ignored). Raises ``ValueError`` on anything that doesn't fit.
    """
    parsed = urlparse(repo_url)
    parts = parsed.path.strip("/").split("/")
    if len(parts) < 2 or not parts[0] or not parts[1]:
        raise ValueError(f"cannot parse owner/repo from {repo_url!r}")
    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[: -len(".git")]
    return owner, repo


def _create_github_pr(
    token: str,
    owner: str,
    repo: str,
    *,
    head: str,
    base: str,
    title: str,
    body: str,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Open a PR via the GitHub REST API. Pure function — no I/O on
    Anthropic side. Returns a dict the agent can read.

    On success: ``{"ok": True, "url": "...", "number": N}``.
    On failure: ``{"ok": False, "status": N, "error": "..."}``.

    Returning structured failures lets the agent decide whether to retry
    (e.g. branch not yet pushed → 422) or escalate.
    """
    payload = json.dumps({"title": title, "body": body, "head": head, "base": base}).encode()
    req = urllib.request.Request(
        url=f"https://api.github.com/repos/{owner}/{repo}/pulls",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — fixed scheme
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code, "error": exc.read().decode("utf-8", "replace")}
    except urllib.error.URLError as exc:
        return {"ok": False, "status": 0, "error": str(exc.reason)}
    return {"ok": True, "url": data.get("html_url"), "number": data.get("number")}


def run_phase(
    client: Anthropic,
    *,
    agent_id: str,
    environment_id: str,
    memory_store_id: str,
    repo_url: str,
    github_token: str,
    phase: Phase,
    human_input: HumanInput = default_human_input,
    stream_to: IO[str] = sys.stdout,
) -> PhaseRun:
    """Run one phase end-to-end and return the grader's terminal result.

    Args:
        client: Anthropic SDK client.
        agent_id: Pre-created agent for this phase. Created by ``setup``.
        environment_id: Shared environment for the cleaning run.
        memory_store_id: Shared memory store. Mounted at
            ``/mnt/memory/cleaner-notes`` so phases can read/write notes.
        repo_url: Target GitHub repository URL.
        github_token: PAT used by Anthropic's git proxy to clone the
            repo and accept branch pushes — *and*, host-side, by the
            orchestrator to open PRs via the GitHub REST API in response
            to ``create_pull_request`` tool calls. Must grant
            ``Contents: Read and write`` and ``Pull requests: Read and
            write`` on the target repo.
        phase: The phase whose rubric drives this session.
        human_input: Implementation of the ``ask_human`` checkpoint.
        stream_to: Where to mirror live agent text (default: stdout). Pass a
            no-op file-like for silent runs.

    Returns:
        A ``PhaseRun`` describing the terminal grader result.
    """
    session = client.beta.sessions.create(
        agent=agent_id,
        environment_id=environment_id,
        title=f"cleaner: {phase.slug} on {repo_url}",
        resources=[
            {
                "type": "github_repository",
                "url": repo_url,
                "authorization_token": github_token,
                "mount_path": "/workspace/repo",
            },
            {
                "type": "memory_store",
                "memory_store_id": memory_store_id,
                "access": "read_write",
                "instructions": (
                    "Cross-phase notes for this cleaning run. Read every "
                    "file under cleaner-notes/ before starting; append new "
                    "findings as you discover them."
                ),
            },
        ],
    )

    # The SDK types last_outcome as a bare string when accessed via
    # getattr; we widen our annotation to match and narrow at the return.
    last_outcome: str | None = None
    last_explanation: str | None = None
    iterations = 0

    # Stream-first: open the stream, *then* send the kickoff. The stream
    # only delivers events emitted after it opens, so reversing this order
    # would silently drop the first few events of the session.
    with client.beta.sessions.events.stream(session_id=session.id) as stream:
        client.beta.sessions.events.send(
            session_id=session.id,
            events=[
                {
                    "type": "user.define_outcome",
                    "description": phase.description,
                    "rubric": {"type": "text", "content": phase.rubric},
                    "max_iterations": phase.max_iterations,
                }
            ],
        )

        for event in stream:
            event_type = getattr(event, "type", None)

            if event_type == "agent.message":
                for block in getattr(event, "content", []) or []:
                    if getattr(block, "type", None) == "text":
                        print(block.text, end="", flush=True, file=stream_to)

            elif event_type == "agent.custom_tool_use":
                # The event union doesn't narrow on the type-string branch,
                # so reach for these fields via getattr. Per the API spec,
                # agent.custom_tool_use always carries id/name/input.
                tool_name = getattr(event, "name", "")
                tool_input = getattr(event, "input", {}) or {}
                tool_use_event_id = getattr(event, "id", "")

                if tool_name == "ask_human":
                    answer = human_input(
                        tool_input.get("question", ""),
                        tool_input.get("context", ""),
                    )
                    client.beta.sessions.events.send(
                        session_id=session.id,
                        events=[
                            {
                                "type": "user.custom_tool_result",
                                "custom_tool_use_id": tool_use_event_id,
                                "content": [{"type": "text", "text": answer}],
                            }
                        ],
                    )
                elif tool_name == "create_pull_request":
                    owner, repo = extract_owner_repo(repo_url)
                    pr_result = _create_github_pr(
                        github_token,
                        owner,
                        repo,
                        head=tool_input.get("head_branch", ""),
                        base=tool_input.get("base_branch", "main"),
                        title=tool_input.get("title", ""),
                        body=tool_input.get("body", ""),
                    )
                    client.beta.sessions.events.send(
                        session_id=session.id,
                        events=[
                            {
                                "type": "user.custom_tool_result",
                                "custom_tool_use_id": tool_use_event_id,
                                "content": [{"type": "text", "text": json.dumps(pr_result)}],
                                "is_error": not pr_result.get("ok", False),
                            }
                        ],
                    )
                else:
                    # Unknown custom tool — surface as an error result so
                    # the agent knows not to retry blindly.
                    client.beta.sessions.events.send(
                        session_id=session.id,
                        events=[
                            {
                                "type": "user.custom_tool_result",
                                "custom_tool_use_id": tool_use_event_id,
                                "content": [
                                    {
                                        "type": "text",
                                        "text": (
                                            f"Unknown custom tool: "
                                            f"{tool_name!r}. The "
                                            "orchestrator only handles "
                                            "ask_human and "
                                            "create_pull_request."
                                        ),
                                    }
                                ],
                                "is_error": True,
                            }
                        ],
                    )

            elif event_type == "span.outcome_evaluation_end":
                iterations = max(iterations, int(getattr(event, "iteration", 0)) + 1)
                last_outcome = getattr(event, "result", None)
                last_explanation = getattr(event, "explanation", None)
                print(
                    f"\n[grader iter {iterations}: {last_outcome}]\n",
                    file=stream_to,
                    flush=True,
                )

            elif event_type == "session.error":
                err = getattr(event, "error", None)
                msg = getattr(err, "message", str(err)) if err else "unknown"
                print(f"\n[session.error] {msg}\n", file=stream_to, flush=True)

            elif event_type == "session.status_terminated":
                break

            elif event_type == "session.status_idle":
                stop = getattr(event, "stop_reason", None)
                stop_type = getattr(stop, "type", None)
                if stop_type == "requires_action":
                    # The session is idling because it needs us (a tool
                    # confirmation or custom tool result). Don't break —
                    # keep draining events until the action is supplied
                    # and the session resumes.
                    continue
                break

    valid_results = {
        "satisfied",
        "needs_revision",
        "max_iterations_reached",
        "failed",
        "interrupted",
    }
    result: OutcomeResult = (
        last_outcome  # type: ignore[assignment]
        if last_outcome in valid_results
        else "terminated_without_outcome"
    )
    return PhaseRun(
        session_id=session.id,
        result=result,
        explanation=last_explanation,
        iterations=iterations,
    )
