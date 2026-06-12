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

- **Custom tool round-trip.** Phases declare an ``ask_human`` custom tool
  for unsafe-to-decide-alone questions. When the agent emits
  ``agent.custom_tool_use`` for it, we reply with
  ``user.custom_tool_result`` keyed by the event's ID.

- **Terminal outcome tracking.** The grader emits
  ``span.outcome_evaluation_end`` with a ``result`` field. We record the
  most recent value; the orchestrator uses it to decide whether to advance.

The module intentionally does not handle stream reconnection. A dropped
stream surfaces as an SDK exception and aborts the phase — for the
hackathon timeframe, restart-by-resume is simpler than mid-flight recovery.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import IO, Literal

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


# Tool definition installed on every phase's agent. Declared here (not on
# Phase) because the spec is invariant across phases — only the prompts
# differ. The agent learns about it from its tools list; the orchestrator
# learns what to expect from this constant.
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


def run_phase(
    client: Anthropic,
    *,
    agent_id: str,
    environment_id: str,
    memory_store_id: str,
    repo_url: str,
    github_token: str,
    phase: Phase,
    vault_ids: list[str] | None = None,
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
        github_token: Token used to clone the repo and push branches. Must
            grant ``Contents: Read and write``; PR creation goes through the
            GitHub MCP server, not this token.
        phase: The phase whose rubric drives this session.
        vault_ids: Vaults holding MCP credentials (notably the GitHub MCP
            OAuth credential used to open PRs). Empty/None means no MCP
            auth — fine if no MCP-backed tool is invoked.
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
        vault_ids=list(vault_ids) if vault_ids else [],
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
                                            "ask_human."
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
