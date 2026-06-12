# Main abstractions

This file is the *what*. For the *why*, see [`design.md`](./design.md).

The cleaner is a thin orchestrator over Managed Agents primitives. Most of
the work — running tools, looping over rubrics, persisting context — is
done by Anthropic-side infrastructure. We supply phase definitions and
drive the event loop.

## The picture

```
┌──────────────────────────────────────────────────────────────────────┐
│                     Anthropic orchestration layer                    │
│             (agent loop — runs the model, calls tools)               │
└─────┬────────────────────────────────────────────────────────────────┘
      │ tool calls
      ▼
┌──────────────────────────┐         ┌──────────────────────────────┐
│  Container (per session) │◀───────▶│  Memory store (shared        │
│  /workspace/repo         │ FUSE    │  across all phases of a run) │
│  bash, file ops, code    │  mount  │  /mnt/memory/cleaner-notes/  │
└──────────────────────────┘         └──────────────────────────────┘
      ▲                                       ▲
      │ events (SSE stream)                   │
      │                                       │
┌─────┴─────────────────────────────────────┬─┴────────────────────────┐
│            cleaner.orchestrator           │       cleaner.state      │
│  setup(repo_url) → env + memstore + 8     │  .cleaner-state/         │
│                    agents (one per phase) │    <repo>.json           │
│  run(repo_url)   → for each phase:        │                          │
│                      cleaner.session      │                          │
└───────────────────────────────────────────┴──────────────────────────┘
```

## Managed Agents primitives we use

### Agent — `src/cleaner/orchestrator.py::Orchestrator.setup`

A persisted, versioned configuration object: model, system prompt, tools,
MCP servers. We create **one agent per phase**, each carrying the phase's
focused system prompt and outcome rubric. Agents are created once at
`setup` time and reused for every run of the cleaning pipeline against
the same target — the IDs are kept in the run's state file.

This matches the skill's strong recommendation: _create once, reference by
ID; do not call `agents.create()` in the request path._

### Environment — `Orchestrator.setup`

A reusable container template. We create **one environment per cleaning
run** (per target repo) with `config.type = "cloud"` and unrestricted
networking. Per-run isolation avoids cross-run pollution of the
container's package cache.

### Session — `src/cleaner/session.py::run_phase`

One stateful interaction with one agent. We create **one session per
phase invocation**. The session mounts:

- the **target repo** as a `github_repository` resource at
  `/workspace/repo`,
- the run's **memory store** as a `memory_store` resource at
  `/mnt/memory/cleaner-notes/` (read-write so the agent can append).

The session terminates when the grader returns a terminal outcome
result, or when the session goes idle with a non-`requires_action` stop
reason.

### Container (per session)

Where the agent's tools actually execute — `bash`, `read`, `write`,
`edit`, `grep`, `glob`, `web_fetch`, `web_search`. The agent loop itself
runs on Anthropic's orchestration layer; tool calls land in the
container as side-effects. We never touch the container directly.

### Memory store — `cleaner-notes/`

The single biggest differentiator vs. "Cursor in a loop." A
workspace-scoped, cross-session filesystem. Phases write findings here:

| File                       | Owner phases       | Content                                              |
| -------------------------- | ------------------ | ---------------------------------------------------- |
| `baseline.md`              | 0                  | Build/test commands, Python version, tooling.        |
| `flagged.md`               | 3, 4               | Code that looks like a bug — preserve or fix later.  |
| `untyped.md`               | 4                  | Functions left without types and why.                |
| `maybe-dead.md`            | 5                  | Dead-code candidates that need a human eye.          |
| `upgrade-plan.md`          | 6                  | Dep upgrades considered, applied, or skipped.        |
| `refactors-applied.md`     | 7                  | Each refactor: rationale, files, line delta.         |

Phase N reads everything written by phases 0..N-1 before starting.

### Outcome event — `user.define_outcome`

Each phase ships its rubric in a `user.define_outcome` event. The Managed
Agents harness runs the agent against the rubric, scores it with an
independent grader, and either iterates (`needs_revision`) or terminates
(`satisfied` / `max_iterations_reached` / `failed`). We send the outcome
once per session and watch for `span.outcome_evaluation_end`.

### MCP server (GitHub) — `Orchestrator.setup`

The GitHub MCP server (`https://api.githubcopilot.com/mcp/`) is declared
on every agent. It exposes tools for opening PRs, creating issues, and
reading repo metadata. Credentials live in a **vault** that the user
attaches to each session via `vault_ids`.

The GitHub MCP credential is **not** the same as the
`authorization_token` on the `github_repository` resource: that token
authenticates `git clone`/`git push` through Anthropic's git proxy, never
touching the container; the MCP credential is what authenticates PR
creation calls.

### Custom tool (`ask_human`) — `src/cleaner/session.py::ASK_HUMAN_TOOL`

The escape hatch for decisions that should not be made autonomously.
Declared on every phase's agent. When the agent calls it, the session
fires `agent.custom_tool_use`; the orchestrator catches the event,
blocks on a `HumanInput` callable (default: stdin), and replies with
`user.custom_tool_result`.

We chose a custom tool rather than relying on prompt scaffolding ("stop
and confirm before X") because a tool call is a hard handshake the model
can't ignore by accident.

## Event-loop contract

Five non-obvious rules baked into `run_phase`:

1. **Stream-first ordering.** Open the SSE stream **before** sending the
   kickoff event. SSE has no replay; events emitted before the stream
   opens are lost.

2. **Correct idle-break gate.** A session transitions to `status_idle`
   transiently while waiting on the human, between parallel tool calls,
   or after the grader runs. Break only on `session.status_terminated`,
   or on `session.status_idle` whose `stop_reason.type` is *not*
   `requires_action`.

3. **Custom-tool round-trip.** `user.custom_tool_result.custom_tool_use_id`
   is the *event ID* of the originating `agent.custom_tool_use`, not a
   `toolu_...` ID.

4. **Outcome event, not user message, for kickoff.** Sending a
   `user.message` alongside `user.define_outcome` would conflict. The
   outcome itself is the instruction.

5. **No reconnection-with-consolidation yet.** A dropped stream aborts
   the phase; resume via re-running `cleaner run`. The state file ensures
   we resume at the right phase.

## State machine — `src/cleaner/state.py` + `cleaner.orchestrator`

```
                  ┌──────────────────────────┐
        setup ──▶ │ env_id + memstore_id +   │ ◀── re-setup is idempotent;
                  │ agent_ids[8]             │     existing IDs are reused
                  └────────────┬─────────────┘
                               │
                  run ────────▶┴────────────┐
                                            ▼
                       ┌──────────────────────────────────┐
                       │ for phase in phases_after(       │
                       │     last_completed_phase):       │
                       │   run_phase(...)                 │
                       │   if result == 'satisfied':      │
                       │     last_completed_phase = slug  │
                       │     save(state)                  │
                       │     continue                     │
                       │   else: stop, return summary     │
                       └──────────────────────────────────┘
```

Single source of truth is the JSON file at
`.cleaner-state/<repo-slug>.json`. Writes are atomic
(write-temp-then-rename) so a kill mid-write can't leave a partial file.

## File map

| File                             | Role                                                                 |
| -------------------------------- | -------------------------------------------------------------------- |
| `src/cleaner/phases.py`          | The 8 phase definitions (system prompt + rubric, ordered).           |
| `src/cleaner/orchestrator.py`    | `setup` + `run` (the pipeline driver).                               |
| `src/cleaner/session.py`         | One phase's session: stream loop, custom tool, idle gate.            |
| `src/cleaner/state.py`           | Per-run JSON state, atomic persistence.                              |
| `src/cleaner/safety.py`          | Refuse to clean this repo itself.                                    |
| `src/cleaner/cli.py`             | `cleaner {setup, run, status}` CLI.                                  |
| `src/cleaner/__main__.py`        | `python -m cleaner` entry.                                           |
| `tests/test_safety.py`           | Smoke tests for the self-cleaning protection.                        |
