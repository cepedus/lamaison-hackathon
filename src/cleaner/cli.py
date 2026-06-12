"""Command-line entry for the project cleaner.

Three subcommands:

- ``setup <repo_url>`` — provision env + memory store + per-phase agents.
- ``run <repo_url>`` — drive the pipeline from the last completed phase.
- ``status <repo_url>`` — print the recorded state for a run.

Credentials flow through environment variables, not flags, so they don't
leak into shell history:

- ``ANTHROPIC_API_KEY`` — required for every subcommand.
- ``CLEANER_GITHUB_TOKEN`` — required for ``run``. Fine-grained PAT with
  ``Contents: Read and write`` on the target repo.
- ``CLEANER_GITHUB_VAULT_ID`` — required for ``run``. Vault holding the
  GitHub MCP OAuth credential used to open PRs.
"""

from __future__ import annotations

import argparse
import os
import sys

from anthropic import Anthropic

from cleaner import state
from cleaner.orchestrator import Orchestrator
from cleaner.phases import PHASES
from cleaner.safety import SelfCleaningRefused


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"error: missing required environment variable {name}", file=sys.stderr)
        sys.exit(2)
    return value


def cmd_setup(args: argparse.Namespace) -> int:
    """Provision resources for a cleaning run."""
    client = Anthropic()
    orch = Orchestrator(client)
    try:
        run_state = orch.setup(args.repo_url)
    except SelfCleaningRefused as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"environment:   {run_state.environment_id}")
    print(f"memory store:  {run_state.memory_store_id}")
    print("agents:")
    for slug, agent_id in run_state.agent_ids.items():
        print(f"  {slug:32s} {agent_id}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Advance the pipeline from the last completed phase."""
    github_token = _require_env("CLEANER_GITHUB_TOKEN")
    vault_id = _require_env("CLEANER_GITHUB_VAULT_ID")
    client = Anthropic()
    orch = Orchestrator(client)
    try:
        summary = orch.run(
            args.repo_url,
            github_token=github_token,
            github_vault_id=vault_id,
        )
    except SelfCleaningRefused as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print("\n=== run summary ===")
    print(f"completed: {summary.completed or '(none)'}")
    if summary.stopped_at:
        print(f"stopped at: {summary.stopped_at}")
        print(f"reason: {summary.last_run.result if summary.last_run else 'unknown'}")
        return 1
    print("pipeline complete.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Print recorded state without making API calls."""
    run_state = state.load(args.repo_url)
    print(f"repo: {run_state.repo_url}")
    print(f"environment:  {run_state.environment_id or '(not provisioned)'}")
    print(f"memory store: {run_state.memory_store_id or '(not provisioned)'}")
    print(f"last completed phase: {run_state.last_completed_phase or '(none)'}")
    print("\nphases:")
    for phase in PHASES:
        agent = run_state.agent_ids.get(phase.slug, "(no agent)")
        session = run_state.sessions.get(phase.slug, "(no session)")
        done = (
            "✓"
            if (
                run_state.last_completed_phase is not None
                and _index_of(phase.slug) <= _index_of(run_state.last_completed_phase)
            )
            else " "
        )
        print(f"  [{done}] {phase.slug:32s} agent={agent}  session={session}")
    return 0


def _index_of(slug: str) -> int:
    for i, phase in enumerate(PHASES):
        if phase.slug == slug:
            return i
    return -1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cleaner",
        description="Run a legacy Python project through a staged, rubric-graded refactor.",
    )
    subs = parser.add_subparsers(dest="command", required=True)

    setup = subs.add_parser("setup", help="Provision env, memory store, and agents.")
    setup.add_argument("repo_url", help="Target GitHub repository URL.")
    setup.set_defaults(func=cmd_setup)

    run = subs.add_parser("run", help="Run the pipeline from the last completed phase.")
    run.add_argument("repo_url", help="Target GitHub repository URL.")
    run.set_defaults(func=cmd_run)

    status = subs.add_parser("status", help="Print the recorded state for a run.")
    status.add_argument("repo_url", help="Target GitHub repository URL.")
    status.set_defaults(func=cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
