# hackathon-lamaison

A staged, rubric-graded project cleaner for legacy Python codebases —
built on the Anthropic [Managed
Agents](https://platform.claude.com/docs/en/managed-agents/overview) API.

Point it at a Python repo and it walks the project through eight ordered
phases — baseline → modernize → safety net → refactor — opening one pull
request per phase, with an outcome rubric gating progress to the next
phase. Findings (architecture notes, flagged bugs, intentional quirks)
accumulate in a memory store shared across phases, so the later
refactor steps build on what the earlier discovery steps learned.

> **Status:** hackathon-scope. The orchestrator, session loop, phase
> definitions, and CLI are in place and pass lint + type-check + tests.
> The pipeline has not yet been driven end-to-end against a real target.
> See [`docs/design.md`](docs/design.md) for the scope cuts.

## The eight phases

```
0. baseline                  — verify build + capture test pass/fail set
1. python-and-uv             — bump Python version, migrate to uv
2. lint-floor                — ruff + pre-commit; one format pass
3. characterization-tests    — pin current behavior; flag suspicious code
4. types                     — pyrefly inference, then refine
5. dead-code                 — vulture + verification
6. deps                      — audit, upgrade outdated & vulnerable
7. refactor                  — targeted, behavior-preserving, human-approved
```

Each phase opens one PR on `cleaner/<NN>-<slug>` and is gated on a
checkable rubric before the next phase starts. See
[`src/cleaner/phases.py`](src/cleaner/phases.py) for the per-phase
system prompts and rubrics.

## Quickstart

Required environment:

| Variable                    | Required for | What it is                                                 |
| --------------------------- | ------------ | ---------------------------------------------------------- |
| `ANTHROPIC_API_KEY`         | all          | Your Anthropic API key.                                    |
| `CLEANER_GITHUB_TOKEN`      | `run`        | Fine-grained PAT with `Contents: Read and write` on target.|
| `CLEANER_GITHUB_VAULT_ID`   | `run`        | Vault holding the GitHub MCP OAuth credential.             |

Provision cloud resources for a target repo (idempotent):

```sh
uv run cleaner setup https://github.com/owner/legacy-project
```

Run the pipeline (resumes from the last completed phase):

```sh
uv run cleaner run https://github.com/owner/legacy-project
```

Inspect the recorded state without making API calls:

```sh
uv run cleaner status https://github.com/owner/legacy-project
```

## Architecture in one paragraph

`setup` creates one environment, one memory store, and one agent per
phase. `run` walks the phases in order, opening one session per phase
that mounts the target repo at `/workspace/repo` and the memory store at
`/mnt/memory/cleaner-notes/`. Each phase sends a `user.define_outcome`
event with its rubric and streams events until the grader terminates.
The agent can call an `ask_human` custom tool for decisions that should
not be made autonomously; the orchestrator handles the round-trip. State
is checkpointed to `.cleaner-state/<repo-slug>.json` after each
successful phase so re-runs resume cleanly.

For the longer story see:

- [`docs/pitch.md`](docs/pitch.md) — _Why this matters._ Market
  size, technical debt cost data, why current AI agents struggle with
  refactoring, and where human-in-the-loop earns its keep.
- [`docs/design.md`](docs/design.md) — _How we got here._ The design
  conversation, the trade-offs we considered, the scope cuts.
- [`docs/abstractions.md`](docs/abstractions.md) — _Main abstractions._
  Reference: every Managed Agents primitive in use, the event-loop
  contract, the file map.

## Constraints baked in

- **Refuses to clean its own repo.** The orchestrator runs
  `cleaner.safety.assert_not_self` before any cloud calls — pointing the
  pipeline at this repo would rewrite the agent definitions out from
  under the running orchestrator.
- **One PR per phase.** Reviewers reject "agent did 800 things." Each
  phase is independently reviewable and revertable.
- **Characterization, not correctness.** Tests and types added by the
  cleaner pin *current* behavior. If code looks like a bug, the agent
  flags it (via `cleaner-notes/flagged.md` + `ask_human`) rather than
  silently fixing it.

## Development

```sh
make ci          # lint + typecheck + tests
make lint
make typecheck
make test
```

The cleaner has its own tests under `tests/`. The first test
(`test_safety.py`) pins the self-cleaning protection — the smallest
load-bearing invariant in the codebase.
