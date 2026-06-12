"""Phase catalog — the ordered pipeline of cleaning steps.

Each phase is a self-contained transformation that:

1. Mounts the target repository in a fresh container,
2. Operates on a feature branch named ``cleaner/<NN>-<slug>``,
3. Produces exactly one pull request,
4. Is graded against an outcome rubric before the next phase starts.

Phases share a memory store across sessions. They write findings to it
(architecture notes, flagged-as-suspicious code, decisions confirmed by a
human) so later phases build on earlier discoveries instead of rediscovering
them. The agent reads/writes this store as ordinary files under
``/mnt/memory/cleaner-notes/``.

The ordering matters: tests and types are the safety net for the real
refactor, so they come first. Dependency upgrades come *after* the safety
net because they routinely break things. See module docstrings in
``orchestrator.py`` for the full rationale.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Phase:
    """One step in the cleaning pipeline.

    Attributes:
        slug: Stable identifier used for branch names and state keys.
            Format: ``NN-kebab-case`` so phases sort lexically by order.
        name: Human-readable name shown in CLI output.
        description: One-line summary of what the phase does.
        system: System prompt installed on the phase's agent. Defines the
            agent's behavior, constraints, and what counts as done.
        rubric: Markdown rubric sent in the ``user.define_outcome`` event.
            Must list explicit, independently checkable criteria — the grader
            scores each criterion on every iteration.
        max_iterations: Cap on grader cycles. Default 5 — phases that
            routinely need more iterations should override.
    """

    slug: str
    name: str
    description: str
    system: str
    rubric: str
    max_iterations: int = 5


# Shared preamble injected into every phase's system prompt. Keeps the
# operating contract (one PR, characterization-first, flag don't fix bugs
# silently) in one place so each phase prompt can focus on its own scope.
_COMMON_PREAMBLE = """\
You are one stage of a multi-phase project cleaner. The repository is mounted
at /workspace/repo on a feature branch you create. You produce exactly one
pull request, then stop.

Operating contract:
- Work on a branch named cleaner/<this phase's slug>. Create it from the
  default branch unless it already exists (in which case resume on it).
- Each commit must leave the project in a buildable, testable state.
- Preserve behavior. If you see code that looks like a bug, do NOT silently
  fix it. Append a note to /mnt/memory/cleaner-notes/flagged.md (one entry
  per finding: file:line, observed behavior, why it looks suspicious) and
  call the ask_human tool if the decision is load-bearing for this phase.
- Do not edit, rename, or delete files outside the repository checkout.
- Memory store contents persist across phases. Read existing notes before
  starting; write new findings as you discover them.

When the rubric is satisfied, push the branch with `git push` and open a
pull request by calling the `create_pull_request` tool. The PR body must
summarize what changed and link to any flagged.md entries this phase
added.
"""


def _system(body: str) -> str:
    """Compose a phase system prompt from the shared preamble and a body."""
    return f"{_COMMON_PREAMBLE}\n\n{body.strip()}\n"


PHASES: list[Phase] = [
    Phase(
        slug="00-baseline",
        name="Baseline",
        description="Verify the project builds and capture the test pass/fail set.",
        system=_system("""
Your job is to establish a reproducible baseline. Do not modify source code.

Discover:
- Python version in use (.python-version, pyproject.toml, setup.py, tox.ini, CI config).
- Package manager (uv, pip+requirements.txt, Pipenv, Poetry, setup.py).
- Test runner (pytest, unittest, nose) and how to invoke it.
- Linters/formatters/type checkers already configured.
- Whether the project currently builds and whether tests currently pass.

Write findings to /mnt/memory/cleaner-notes/baseline.md, including the exact
commands you ran and their output. If the project does not build, document
the smallest set of changes needed to make it build (do not apply them
yourself — open an issue describing them and stop).
"""),
        rubric="""\
- /mnt/memory/cleaner-notes/baseline.md exists and lists: Python version,
  package manager, test runner, lint/type tools, and current build status.
- The exact commands used to install dependencies and run tests are recorded
  verbatim (so later phases can re-run them).
- A snapshot of the current test results (counts of passed/failed/skipped/
  errored) is recorded.
- No source files outside /mnt/memory/ have been modified.
- A PR is opened on branch cleaner/00-baseline containing only the notes
  file (committed under docs/ or a similar non-intrusive location), OR the
  baseline is reported as "no PR needed; notes are in the memory store" if
  the repository's conventions discourage committing meta documentation.
""",
        max_iterations=3,
    ),
    Phase(
        slug="01-python-and-uv",
        name="Python version and uv",
        description="Bump to a supported Python version and migrate package management to uv.",
        system=_system("""
You perform two related migrations:

1. If the project targets an unsupported Python version (3.8 or older as of
   2026), bump to a supported one. Prefer the lowest version that still
   supports the project's hard runtime constraints. Update .python-version,
   pyproject.toml's requires-python, and any CI config.

2. Migrate package management to uv. Preserve every dependency at its
   current pinned version. Map dev/test/docs groups into
   [dependency-groups] in pyproject.toml. Generate uv.lock with `uv lock`.

Order matters: bump Python first (small, easy to verify), commit, then do
the uv migration. Two commits, one PR.

Verify after each commit by running the test command from baseline.md. Tests
must produce the same pass/fail set as the baseline snapshot.
"""),
        rubric="""\
- pyproject.toml has [project] requires-python set to a supported version.
- A uv.lock file exists and is consistent with pyproject.toml (`uv lock --check` passes).
- All dependencies present before the migration are present after, at the
  same versions (or compatible patch upgrades if a version is yanked).
- Dev/test/docs dependencies are grouped under [dependency-groups], not
  mixed with runtime dependencies.
- `uv sync` succeeds in a fresh checkout.
- `uv run` invocation of the test command from baseline.md produces the
  same pass/fail counts as the baseline snapshot (no new failures).
- One PR opened on branch cleaner/01-python-and-uv.
""",
    ),
    Phase(
        slug="02-lint-floor",
        name="Lint and format floor",
        description="Install ruff + pre-commit. No code style changes beyond what the formatter applies.",
        system=_system("""
Install a baseline quality floor:

- Configure ruff for linting and formatting. Pick a conservative rule set
  (default ruff rules + isort). Do not enable opinionated rules that would
  produce a massive diff.
- Set ruff's line length and target Python version to match the project.
- Add a pre-commit config that runs ruff check and ruff format.
- Apply `ruff format` once across the codebase. This is the only code-style
  change permitted in this phase.

Explicitly out of scope: fixing lint warnings that ruff cannot auto-fix.
Leave them for the agent to surface in the PR description as a follow-up
list.
"""),
        rubric="""\
- pyproject.toml contains a [tool.ruff] section with sensible defaults
  (line-length, target-version matching requires-python, lint rule set).
- A .pre-commit-config.yaml exists and runs ruff check + ruff format.
- `uv run ruff check` passes (any remaining warnings are listed in the PR
  description, not silenced via ignores).
- `uv run ruff format --check` passes (everything is formatted).
- The test command from baseline.md still produces the same pass/fail
  counts.
- One PR opened on branch cleaner/02-lint-floor. The PR body lists every
  unsuppressed remaining warning as a follow-up checklist.
""",
    ),
    Phase(
        slug="03-characterization-tests",
        name="Characterization tests",
        description="Write tests that pin current behavior. Flag — do not fix — anything that looks like a bug.",
        system=_system("""
Build a behavior-pinning test suite.

Approach:
- For each public function/class in the codebase, write at least one test
  that exercises the happy path. For functions with branches, cover the
  primary branches.
- Tests must reflect *current* behavior, even if that behavior looks wrong.
  If a function returns None on error inputs, write a test asserting it
  returns None. Do NOT change the function.
- When you encounter behavior that looks suspicious (silent failures,
  inconsistent error handling, off-by-one boundaries, comparisons against
  the wrong type, etc.), append an entry to
  /mnt/memory/cleaner-notes/flagged.md and call ask_human(
    question="Is this a bug to preserve, or a bug to fix later?",
    context="<file:line, the suspicious snippet, the test you would write>",
  ).
- Prefer black-box tests at module boundaries over white-box tests of
  internals. Internal implementations will change in later phases; the
  black-box behavior should not.

Target coverage: at least 80% line coverage on every module that contains
behavior (i.e., excluding __init__.py shims and trivial dataclass modules).
No coverage gate is enforced on test files themselves.
"""),
        rubric="""\
- Every public function in non-trivial modules has at least one test
  exercising it.
- Line coverage on behavior-bearing modules is at least 80%.
- Tests pass under the test command from baseline.md.
- /mnt/memory/cleaner-notes/flagged.md exists. Every entry has: file:line,
  the suspicious snippet, observed behavior, and the disposition decided by
  ask_human (preserve / fix-later / not-a-bug).
- No production code (non-test files) has been modified except by ruff
  format from phase 02.
- One PR opened on branch cleaner/03-characterization-tests. The PR body
  links to the flagged.md entries.
""",
        max_iterations=7,
    ),
    Phase(
        slug="04-types",
        name="Type annotations",
        description="Add type annotations via pyrefly inference, then refine Any blobs.",
        system=_system("""
Add type annotations in two passes:

Pass 1: Run pyrefly on the codebase. Apply its suggestions where they are
unambiguous (concrete types, no widening to Any). Commit.

Pass 2: For every signature still missing types or annotated `Any`/
`dict[str, Any]`/`object`, infer a tighter type from:
  - The characterization tests written in phase 03 (call sites are ground
    truth for input shape).
  - Constructor/return patterns within the function.
  - Type stubs available for third-party libraries.

If a signature genuinely has heterogeneous input — e.g. accepts both a Path
and a string — use a Union, not Any. If you cannot determine a type with
high confidence, leave it unannotated and add an entry to
/mnt/memory/cleaner-notes/untyped.md explaining why.

Run pyrefly after every batch of changes. The number of pyrefly errors
must be monotonically non-increasing across commits — no commit may
introduce new errors.

Do not change behavior. If pyrefly reveals what looks like a bug
(unreachable branch, narrowing that doesn't hold), flag in flagged.md and
ask_human; do not silently fix.
"""),
        rubric="""\
- Every public function has a complete signature (all parameters and the
  return type annotated).
- The percentage of internal functions with complete signatures is at
  least 80%, OR every unannotated internal function has a justifying entry
  in /mnt/memory/cleaner-notes/untyped.md.
- `uv run pyrefly check` produces zero errors (warnings acceptable).
- Tests from phase 03 still pass with the same pass/fail counts.
- No use of `Any` outside of justified cases in untyped.md.
- One PR opened on branch cleaner/04-types.
""",
        max_iterations=8,
    ),
    Phase(
        slug="05-dead-code",
        name="Dead code removal",
        description="Remove provably unused code. Confidence required.",
        system=_system("""
Find and remove dead code, with a high confidence bar.

Discovery:
- Run vulture to surface candidates.
- For each candidate, verify it is truly unreachable: check for dynamic
  references (getattr, __all__, registry decorators, plugin entry points,
  template references, CLI arg parsing tables, ORM model collection).
- Check the repo's __init__.py re-exports — a "private" function may be
  part of the public API surface via re-export.

Removal:
- Remove only candidates that are unambiguously unreachable. Borderline
  cases go to /mnt/memory/cleaner-notes/maybe-dead.md with ask_human
  consultation.
- Group removals by logical unit (a whole class, a whole module) rather
  than line-by-line, so reviewers can evaluate intent.
- After every removal, the tests from phase 03 must still pass and pyrefly
  must still produce zero errors.

Do not remove:
- Public API symbols, even if no internal caller exists (external callers
  may exist).
- Symbols listed in __all__.
- Functions decorated with framework decorators (Flask routes, Celery
  tasks, click commands, pytest fixtures).
"""),
        rubric="""\
- vulture reports no more dead-code candidates above 90% confidence.
- No public symbol has been removed (verify against the symbols in
  baseline.md and __all__ lists).
- Tests pass with the same pass/fail counts as phase 03 established.
- pyrefly produces zero errors.
- /mnt/memory/cleaner-notes/maybe-dead.md lists every ambiguous candidate
  not removed, with the reasoning.
- One PR opened on branch cleaner/05-dead-code.
""",
    ),
    Phase(
        slug="06-deps",
        name="Dependency audit and upgrade",
        description="Upgrade outdated and vulnerable dependencies. Tests are the safety net.",
        system=_system("""
Audit and upgrade dependencies.

Discovery:
- Run `uv pip list --outdated` (or equivalent) and `pip-audit` against the
  lockfile to find outdated and vulnerable packages.
- For each candidate upgrade, read its changelog between the current and
  latest versions. Note breaking changes in
  /mnt/memory/cleaner-notes/upgrade-plan.md.

Upgrade:
- Upgrade one logical group per commit (a package and its plugins, a
  framework and its ecosystem). Re-run tests after each commit.
- For major-version bumps with documented breaking changes, plan the
  migration in upgrade-plan.md first, ask_human to confirm, then apply.
- If an upgrade breaks tests, either fix the breakage (if the test breakage
  reflects a real API change you can accommodate) or pin the dependency at
  the highest working version and document why.

Order:
- Security-critical upgrades first (CVE'd packages).
- Then non-breaking minor/patch bumps.
- Then major-version bumps requiring code changes.

Tests are the safety net. Do not weaken or skip tests to make an upgrade
pass. If a test starts failing after an upgrade, either revert the upgrade
or fix the underlying code change that caused the failure.
"""),
        rubric="""\
- pip-audit reports zero known vulnerabilities in production dependencies.
- /mnt/memory/cleaner-notes/upgrade-plan.md lists every package considered,
  the version moved from/to, the reason for upgrading or skipping, and any
  breaking changes addressed.
- Tests pass with the same pass/fail counts as phase 03 established.
- The dependency lockfile (uv.lock) is regenerated and committed.
- One PR opened on branch cleaner/06-deps.
""",
        max_iterations=10,
    ),
    Phase(
        slug="07-refactor",
        name="Architectural refactor",
        description="Apply targeted refactors guided by the architecture notes accumulated in earlier phases.",
        system=_system("""
The previous phases established a safety net and accumulated findings in
the memory store. Now apply targeted, behavior-preserving refactors.

Read first:
- /mnt/memory/cleaner-notes/baseline.md — what the project is.
- /mnt/memory/cleaner-notes/flagged.md — known suspicious behavior.
- /mnt/memory/cleaner-notes/upgrade-plan.md — breaking changes already
  absorbed.

Identify refactor candidates by analyzing:
- Cohesion: modules that contain unrelated responsibilities.
- Coupling: modules that import from many unrelated places.
- Duplication: near-identical code blocks across files.
- Outdated patterns: bare `except`, `os.path` instead of pathlib, manual
  string formatting that could be f-strings, `typing.List` rather than
  `list`, etc.

Prioritize refactors that:
- Are small enough to review in under 15 minutes.
- Are covered by existing tests (so the safety net catches regressions).
- Address a finding already in flagged.md or improve a hotspot identified
  in the architecture notes.

For each refactor candidate, call ask_human with a one-line summary, the
expected diff size, and the risk level. Apply approved refactors only.

Do not:
- Introduce new abstractions for code that has only one caller.
- Add configuration knobs no one asked for.
- Rename public API symbols.
- "Improve" code that is fine, just to leave a mark.
"""),
        rubric="""\
- Every applied refactor is approved via ask_human.
- /mnt/memory/cleaner-notes/refactors-applied.md lists each refactor:
  rationale, files touched, line delta, and the linked flagged.md entry
  (if any).
- Tests pass with the same pass/fail counts as phase 03 established.
- pyrefly produces zero errors.
- No public API symbol has been renamed or removed.
- One PR opened on branch cleaner/07-refactor.
""",
        max_iterations=10,
    ),
]


def by_slug(slug: str) -> Phase:
    """Look up a phase by its slug. Raises KeyError if not found."""
    for phase in PHASES:
        if phase.slug == slug:
            return phase
    raise KeyError(f"unknown phase: {slug!r}")
