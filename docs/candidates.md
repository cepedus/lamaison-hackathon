# Candidate projects to clean

Candidates for driving the cleaner end-to-end. Each one is real,
recognizable, abandoned-or-archived, and small enough to fit a hackathon
demo window.

## What makes a good candidate

For a demo, the target needs to hit several constraints at once:

- **Recognizable.** Judges should react "I've used that" or "I've heard
  of that." A no-name repo wastes the framing of "this is real legacy
  pain."
- **Genuinely stale.** Archived on GitHub, or last release more than two
  years ago, *and* still has meaningful PyPI download volume. The mix
  matters — abandoned-and-popular is the story.
- **Small.** Phases 0–3 (baseline → tests) need to complete in under 20
  minutes for a live demo. Target: under ~2,000 LOC across the
  installable package, ideally a single module or a handful of files.
- **Testable in a container with no external services.** No databases,
  no remote services, no GUI. Pure Python computations or local I/O.
- **Forkable.** Permissive license; we push branches and open PRs to
  *our* fork, not the original.
- **Real surface for each phase.** Has obvious tooling debt (no
  pyproject, no uv lock, no ruff config), missing types, missing or
  flaky tests, and some clear refactor targets.

## Tier 1 — top picks

### `fuzzywuzzy` (seatgeek/fuzzywuzzy)

**The pitch in one line:** archived August 2024, ~17M downloads per
month, still being used in production all over the world.

- **Status:** Repository archived (read-only) on Aug 26, 2024.
- **Usage:** ~16.9M downloads/month on PyPI as of recent stats — the
  archival did not slow usage at all.
- **Size:** Small (single core module + a handful of helpers).
- **Successor narrative:** `RapidFuzz` is the maintained drop-in
  replacement. Lets the cleaner produce a deps-phase PR with a
  meaningful upgrade story.
- **License:** GPLv2 — check that this is compatible with your demo
  setup. (Forks and PRs are fine; redistribution may have constraints.)

**Why it demos well:** the "17M downloads a month, archived" stat is
the single most compelling hook we can put on a slide. The successor
project (RapidFuzz) gives phase 6 (deps) a concrete migration target.

### `docopt` (docopt/docopt)

**The pitch in one line:** the original "CLI from a docstring" library;
last release 2014; the maintainer publicly conceded the project is dormant.

- **Status:** Public discussion (issues #371 "Is docopt maintained?",
  #462 "State of docopt for python?") confirms the project is dormant.
  A community fork (`docopt-ng`) is the active alternative.
- **Usage:** Still a hard dependency for many older CLI tools across
  the ecosystem.
- **Size:** Famously single-file (`docopt.py`).
- **Successor narrative:** `docopt-ng` provides the deps-phase upgrade
  target.

**Why it demos well:** the codebase is small enough that we could
plausibly run *all 8 phases* in a demo window, not just the first few.
The "single-file library, ~8k stars, no release since 2014" framing
lands instantly.

**Caveat:** docopt is already largely Python-3-friendly, so phase 1
(Python+uv) is less dramatic here than on a Python-2-flavored target.
Compensate by emphasizing phases 2 (lint), 3 (tests), and 4 (types).

### `HappyBase` (wbolster/happybase)

**The pitch in one line:** the canonical Python client for Apache
HBase; explicitly marked unmaintained by the author.

- **Status:** Confirmed unmaintained per the maintainer's own
  statement (referenced in PyPI archival discussions).
- **Usage:** Still pulled in by data-engineering pipelines that need
  HBase access.
- **Size:** Moderate — larger than docopt or fuzzywuzzy but still in
  the demo-window range.
- **Caveat:** The test suite likely requires a live HBase
  (Thrift) server, which complicates phase 0 baseline. If you can't
  stand up HBase in the container, drop this and pick something with
  pure-Python tests.

## Tier 2 — backups

Solid options if the Tier 1 picks don't pan out for some reason
(license, test infrastructure, scope).

| Project               | Why it qualifies                                                                      | Caveat                                                               |
| --------------------- | ------------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| `pandas-datareader`   | Explicitly listed as unmaintained on PyPI discussions; real users still depend on it. | Many tests hit live financial-data endpoints — flaky in a container. |
| `PDFMiner` (original) | Original `pdfminer` is abandoned; `pdfminer.six` is the maintained fork.              | Larger codebase; phase windows could overrun.                        |
| `appdirs`             | Archived; `platformdirs` is the maintained successor.                                 | Tiny — might be *too* small to demo full pipeline.                   |
| `Unipath`             | Archived; `pathlib` is the modern stdlib equivalent.                                  | Tiny + the migration target is stdlib, so phase 7 looks weird.       |
| `scandir`             | Archived; `os.scandir` is now in stdlib.                                              | Same problem as Unipath — modernization = "delete the package."      |

The "tiny + stdlib successor" group (appdirs / Unipath / scandir) is a
clean story but doesn't give the cleaner enough surface area to show
off. Pick them only if the demo needs to be very short.

## Explicit non-candidates

A few projects to avoid even though they look tempting:

- **`requests`, `urllib3`, `jsonschema`, `pyyaml`, `flask`** — actively
  maintained. Pointing the cleaner at these would step on real
  maintainers' work and produce noise PRs.
- **`six`** — its job is literally Python 2/3 compatibility; cleaning
  it up means deleting it. Not a useful demo of *modernization*.
- **Any large monorepo (Django, Salt, Ansible).** Too big for the demo
  window; phase 0 alone would take an hour.
- **Anything requiring a GPU, a cloud service, or a desktop GUI.** The
  container can't satisfy those dependencies.

## How to evaluate a new candidate

Before running the cleaner against any candidate, check:

- [ ] **License permits redistribution under your demo conditions.**
  GPL projects are fine to fork and PR; redistributing modified
  versions has constraints. MIT/BSD/Apache are simpler.
- [ ] **Repository is `archived` on GitHub, or the last release is
  >24 months old, or the maintainer has publicly stepped back.** Don't
  point the cleaner at someone's active side project.
- [ ] **`pip install <pkg>` works in a fresh Python 3.14 venv.** If
  the package can't even install, phase 0 will report a blocked
  baseline and stop — fine for the pipeline, bad for a demo.
- [ ] **The test suite runs in a container with no external services.**
  Run `python -m pytest` in a fresh checkout. If it depends on
  Postgres / Redis / a remote API, either inject mocks or pick
  something else.
- [ ] **Total LOC under ~2,000.** `tokei` or `cloc` gives a quick
  count. Bigger codebases will overrun the demo window.
- [ ] **Fork the repo to a sandbox account before running.** The
  cleaner pushes branches and opens PRs; you don't want those landing
  on the original owner's organization.

## Recommended demo path

Start with **fuzzywuzzy** as the headliner — the "17M downloads,
archived" hook is too good to pass up. Use **docopt** as the
"we ran the full 8-phase pipeline to completion" fallback because of
its small size. Keep **HappyBase** in reserve only if you have an HBase
test harness already.

Then for live demo flow:

1. Fork fuzzywuzzy → `your-org/fuzzywuzzy-demo`.
2. `cleaner setup https://github.com/your-org/fuzzywuzzy-demo`.
3. `cleaner run https://github.com/your-org/fuzzywuzzy-demo`.
4. Show the PRs landing one-by-one with green CI on each.

If the demo budget tightens, run phases 0–3 only and explain that the
remaining phases work the same way.
