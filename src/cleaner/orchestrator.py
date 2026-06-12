"""Setup and run loop for a cleaning pipeline.

``setup`` provisions the cloud resources for a run (environment, memory
store, one agent per phase) and persists their IDs. It is idempotent: a
re-run only creates resources that don't already have a recorded ID.

``run`` advances the pipeline phase by phase. Each phase opens a session,
streams events until the grader produces a terminal result, and either
advances to the next phase (``satisfied``) or stops (anything else). State
is checkpointed after each successful phase so a later ``run`` resumes
from where the previous left off.

Two design choices worth flagging:

- **One environment per run**, not one shared environment. Environments are
  cheap to create and isolating them per target repo avoids cross-run
  pollution of cached packages and container state.

- **One agent per phase, shared across runs of the same repo**. Per-phase
  agents let each phase carry its own focused system prompt without
  re-rendering it on every session. They're stored on the run's state file,
  so re-running the same repo reuses them — keeps the agent IDs stable and
  the version history useful.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import IO, Any, cast

from anthropic import Anthropic

from cleaner import safety, state
from cleaner.phases import PHASES, Phase
from cleaner.session import (
    ASK_HUMAN_TOOL,
    CREATE_PR_TOOL,
    HumanInput,
    PhaseRun,
    default_human_input,
    run_phase,
)

MODEL = "claude-opus-4-7"
"""Model used for every phase agent. Opus 4.7 is the recommended default
for coding and agentic work — it follows the rubrics carefully and uses
tools well. Override on a per-phase basis if a phase's cost profile
warrants it.
"""


@dataclass(frozen=True, slots=True)
class RunSummary:
    """Result of one ``Orchestrator.run`` call.

    Attributes:
        completed: Slugs of phases that ran ``satisfied`` during this call.
        stopped_at: Slug of the phase that returned a non-satisfied result
            and halted the pipeline, or ``None`` if the pipeline reached
            the end successfully.
        last_run: Detailed result of the most recent phase attempted. Use
            ``last_run.session_id`` to deep-link into the Console.
    """

    completed: list[str]
    stopped_at: str | None
    last_run: PhaseRun | None


class Orchestrator:
    """Driver for the full cleaning pipeline.

    Stateless apart from the injected ``client``: all per-run state lives
    in the JSON file managed by ``cleaner.state``. Constructing a new
    ``Orchestrator`` for a previously-seen repo URL resumes that run.
    """

    def __init__(self, client: Anthropic) -> None:
        self.client = client

    def setup(self, repo_url: str) -> state.RunState:
        """Provision cloud resources for ``repo_url``. Idempotent.

        Creates (only as needed): one environment, one memory store, and
        one agent per phase. Persists IDs to the run's state file so
        subsequent calls reuse them.

        Args:
            repo_url: Target repository. Must not be the cleaner's own repo
                (see ``cleaner.safety``).

        Returns:
            The updated, persisted run state.

        Raises:
            cleaner.safety.SelfCleaningRefused: If ``repo_url`` matches the
                cleaner's own repo.
        """
        safety.assert_not_self(repo_url)
        run_state = state.load(repo_url)

        if not run_state.environment_id:
            env = self.client.beta.environments.create(
                name=f"cleaner-{state.slug_for_url(repo_url)}"[:64],
                config={
                    "type": "cloud",
                    "networking": {"type": "unrestricted"},
                },
            )
            run_state.environment_id = env.id

        if not run_state.memory_store_id:
            store = self.client.beta.memory_stores.create(
                name=f"cleaner-notes-{state.slug_for_url(repo_url)}"[:64],
                description=(
                    "Cross-phase findings for one cleaning run. Phases "
                    "read prior notes before starting and append new "
                    "findings under cleaner-notes/."
                ),
            )
            run_state.memory_store_id = store.id

        for phase in PHASES:
            if phase.slug in run_state.agent_ids:
                continue
            # The Tool union is heterogeneous; the SDK accepts these dict
            # shapes per the docs but pyrefly can't narrow them through
            # the literals. Cast at the boundary.
            tools = cast(
                Any,
                [
                    {
                        "type": "agent_toolset_20260401",
                        "default_config": {"enabled": True},
                    },
                    ASK_HUMAN_TOOL,
                    CREATE_PR_TOOL,
                ],
            )
            agent = self.client.beta.agents.create(
                name=f"cleaner/{phase.slug}",
                description=phase.description,
                model=MODEL,
                system=phase.system,
                tools=tools,
            )
            run_state.agent_ids[phase.slug] = agent.id

        state.save(run_state)
        return run_state

    def run(
        self,
        repo_url: str,
        *,
        github_token: str,
        human_input: HumanInput = default_human_input,
        stream_to: IO[str] = sys.stdout,
    ) -> RunSummary:
        """Advance the pipeline from the last completed phase to the end.

        Args:
            repo_url: Target repository. Must have been set up already.
            github_token: PAT with ``Contents: Read and write`` and
                ``Pull requests: Read and write`` on the target repo.
                Used by Anthropic's git proxy for clone/push, and by the
                orchestrator host-side for opening PRs via the GitHub
                REST API. Never enters the session container.
            human_input: Implementation of the ``ask_human`` checkpoint.
                Default reads from stdin.
            stream_to: Where to mirror live agent text. Default stdout.

        Returns:
            A ``RunSummary`` describing what completed and where (if
            anywhere) the pipeline halted.

        Raises:
            cleaner.safety.SelfCleaningRefused: If ``repo_url`` matches the
                cleaner's own repo.
            RuntimeError: If the run has not been set up.
        """
        safety.assert_not_self(repo_url)
        run_state = state.load(repo_url)
        self._require_setup(run_state)

        completed: list[str] = []
        last_run: PhaseRun | None = None
        remaining = _phases_after(run_state.last_completed_phase)

        for phase in remaining:
            print(
                f"\n=== phase {phase.slug}: {phase.name} ===\n",
                file=stream_to,
                flush=True,
            )
            last_run = run_phase(
                self.client,
                agent_id=run_state.agent_ids[phase.slug],
                environment_id=run_state.environment_id,  # type: ignore[arg-type]
                memory_store_id=run_state.memory_store_id,  # type: ignore[arg-type]
                repo_url=repo_url,
                github_token=github_token,
                phase=phase,
                human_input=human_input,
                stream_to=stream_to,
            )
            run_state.sessions[phase.slug] = last_run.session_id
            state.save(run_state)

            if last_run.result != "satisfied":
                print(
                    f"\nphase {phase.slug} halted: {last_run.result}\n"
                    f"{last_run.explanation or '(no explanation)'}\n",
                    file=stream_to,
                    flush=True,
                )
                return RunSummary(completed=completed, stopped_at=phase.slug, last_run=last_run)

            run_state.last_completed_phase = phase.slug
            state.save(run_state)
            completed.append(phase.slug)

        return RunSummary(completed=completed, stopped_at=None, last_run=last_run)

    @staticmethod
    def _require_setup(run_state: state.RunState) -> None:
        missing: list[str] = []
        if not run_state.environment_id:
            missing.append("environment_id")
        if not run_state.memory_store_id:
            missing.append("memory_store_id")
        if len(run_state.agent_ids) < len(PHASES):
            missing.append("agent_ids")
        if missing:
            raise RuntimeError(
                "run state is not initialized (missing: "
                f"{', '.join(missing)}). Run `cleaner setup <repo_url>` first."
            )


def _phases_after(last_completed: str | None) -> list[Phase]:
    """Phases strictly after ``last_completed`` in declaration order."""
    if last_completed is None:
        return list(PHASES)
    seen = False
    out: list[Phase] = []
    for phase in PHASES:
        if seen:
            out.append(phase)
        if phase.slug == last_completed:
            seen = True
    if not seen:
        raise RuntimeError(
            f"unknown last_completed_phase {last_completed!r} — state file is "
            "out of sync with the phase catalog."
        )
    return out
