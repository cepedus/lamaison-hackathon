# How we got here

This file is the narrative — the design conversation behind the code, the
trade-offs we considered, and the reasoning for the choices that landed.
For the *what* (file-by-file, abstraction-by-abstraction) see
[`abstractions.md`](./abstractions.md).

## The starting point

A general hackathon brief — _use Managed Agents for something painful_ —
narrowed to one concrete idea: take a legacy, potentially abandoned Python
project and progressively modernize it. The user's first sketch:

1. Migrate package management to `uv`.
2. Add type annotations using a static analyzer (e.g. pyrefly).
3. Generate or augment tests where coverage is thin.
4. Once behavior is _known_ (via tests) and _explicit_ (via types), do the
   real refactor: architecture, patterns, dead code, docs.

The instinct that makes this idea worth building: **build the safety net
before changing anything**. Most refactor agents skip straight to step 4
and rewrite code with no way to detect regressions. Tests-and-types first
is what makes the subsequent refactor safe.

## The insight that drove the design: characterization vs. correctness

The catch with steps 2 and 3 is that **tests and types inferred from
existing code enshrine *current* behavior, including bugs**.

- Pyrefly tells you what the code _does_, not what was intended. A
  function that returns `None | dict` because someone forgot to handle a
  case will get typed as `None | dict` — locking the bug in.
- Auto-generated tests for that same function will assert it returns
  `None` on the bad input. Now the bug is enshrined by a test.

That's actually what you want — for safe refactoring, you must preserve
behavior. But it means the agent has to be *honest* about what it's doing:
it's writing **characterization tests** (behavior-pinning), not
**correctness tests**. When it sees something that looks like a bug, the
correct response is _flag, don't fix_:

- Append to `/mnt/memory/cleaner-notes/flagged.md`.
- Call the `ask_human` custom tool for any load-bearing decision.

This is the seed for two of the bigger architectural choices below
(memory store and human checkpoint).

## The reordering

The user's original sequence was uv → types → tests → refactor. We landed
on something different. The ordering matters because each phase relies on
guarantees the previous phases established.

```
0. baseline                     ← can we build? does the test suite run?
1. python version + uv          ← upgrade runtime, modernize tooling
2. lint floor (ruff + pre-commit)  ← cheap quality bar, sets review-noise floor
3. characterization tests       ← the safety net
4. types (pyrefly + refine)     ← now safe to add — tests catch widening errors
5. dead code (vulture + verify) ← high-confidence removals only
6. dep audit + upgrade          ← *after* the safety net, because deps break things
7. real refactor                ← targeted, behavior-preserving, human-approved
```

Worth calling out:

- **Baseline (phase 0) is non-trivial.** If the project doesn't build, you
  can't measure regression. Phase 0 either confirms a working baseline or
  reports back the smallest set of changes needed — it doesn't apply them.

- **Tests come before types**, not the other way around. Types added
  without a test net are unsafe: pyrefly will widen-to-`Any` on the slightest
  ambiguity, and there's no signal that the widening is wrong. With tests
  in place, type refinements that change runtime behavior surface as
  failures.

- **Dependency upgrades come *after* the safety net**, not before. Dep
  upgrades are the most likely thing to break runtime behavior, and the
  test suite is what tells us whether they did.

- **Refactor is last** and explicitly scoped: read the accumulated notes
  from earlier phases, propose specific small changes via `ask_human`,
  apply only the approved ones. No speculative cleanup, no abstractions
  for code that has one caller.

## Trade-offs we made consciously

**One PR per phase, not one giant transformation.** Reviewers reject "the
agent did 800 things." Each phase opens its own branch
(`cleaner/<NN>-<slug>`) and its own PR. Phase N+1 starts from the merged
result of phase N. This is a hard rule baked into every phase's system
prompt and rubric.

**Memory store across phases, not a per-session note-taker.** Each phase
writes findings to a workspace-scoped memory store. Phase 7 (refactor)
reads what phase 0 (baseline) discovered about architecture and what
phase 3 (tests) flagged as suspicious. Without this, each phase
re-discovers the same context — and the agent forgets the maintainer's
prior confirmations about which "quirks" are intentional. The store is
the load-bearing differentiator vs. "Cursor in a loop."

**Rubric per phase, not one rubric for the whole pipeline.** Each phase
has its own explicit, checkable success criteria sent in
`user.define_outcome`. The grader scores against them independently. A
single end-of-pipeline rubric would be too vague to drive iteration ("the
project should be cleaner" is not gradeable); per-phase rubrics also let
us halt cleanly on the first phase that fails its bar.

**Human checkpoint via custom tool, not via prompt scaffolding.** The
agent calls `ask_human(question, context)` for decisions that should not
be made autonomously: rename a public API? preserve this bug or schedule
it? delete this 2000-line module? The orchestrator catches the
`agent.custom_tool_use` event, blocks on stdin, and returns the human's
answer. This is more reliable than asking the model to "stop and confirm
before doing X" via the system prompt — that's a hint the model can
ignore. A tool call is a hard handshake.

**Resumable runs via a JSON state file.** Each cleaning run keeps a
single state file at `.cleaner-state/<repo-slug>.json` recording
environment ID, memory store ID, per-phase agent IDs, and the slug of the
last successfully completed phase. Re-running `cleaner run` against the
same repo resumes from the next phase. Cloud resources are never
duplicated on re-run.

**Refuse to clean this repo itself.** The orchestrator runs an explicit
self-cleaning check (`cleaner.safety.assert_not_self`) before doing
anything. The cleaner is a Python project; pointing it at itself would
rewrite the agent definitions out from under the running orchestrator.

## What we built and what's next

What's wired up:

- Eight phase definitions with system prompts and outcome rubrics.
- Orchestrator: idempotent `setup`, resumable `run`.
- Session runner with the correct event-loop semantics (stream-first
  ordering, requires-action idle gate, custom-tool round-trip).
- Persistent state, safety check, lint + typecheck + tests all green.

Explicit scope cuts for this session:

- **No reconnect-with-consolidation on dropped SSE streams.** Today, a
  drop aborts the phase; the human re-runs to resume. Next step:
  implement the consolidation pattern from
  `managed-agents-events.md` → Reconnecting after a dropped stream.

- **No vault provisioning helper.** The user creates the GitHub MCP vault
  out-of-band (Console or `ant beta:vaults create`) and passes the ID via
  `CLEANER_GITHUB_VAULT_ID`. A `cleaner provision-vault` subcommand could
  automate this.

- **No actual run against a live target.** The API surface is exercised
  by type-check and import only; nothing has been driven end-to-end yet.
  The natural first run is against a small, real, fork-able legacy
  package — phase 0 (read-only) is the cheapest probe.

- **No phase-by-phase model overrides.** Every phase uses Opus 4.7.
  Phase 2 (lint floor) and phase 5 (dead code) are mostly mechanical
  and could likely run on Haiku 4.5 for substantial cost savings — easy
  follow-up.
