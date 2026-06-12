# Pitch — staged refactor of legacy Python projects

## The one-liner

Most legacy codebases never get cleaned up because the safe way is too
slow and the fast way is too dangerous. We use Managed Agents to do the
slow-and-safe way at machine speed — one PR per phase, a rubric gating
each, and a human in the loop only on the decisions that actually need
one.

## The pain is large and well-documented

**Developers spend a third to nearly half their time on legacy code and
technical debt.**

- Stripe's *Developer Coefficient* study put it at **~33% of developer
  time globally**; other industry research lands in the **20–42%**
  range.[^stripe][^bitvea]
- McKinsey reports **30% of CIOs say more than 20% of their
  new-product budget is diverted to debt-related work** — before a single
  line of new code ships.[^mckinsey-via-devico]
- In severe cases, **up to ~75% of a tech budget** can go to legacy
  maintenance alone.[^unqork]

**The bill at the runtime layer is concrete and quantifiable.** A
mid-market company on an outdated Python (3.10 → 3.13) can save roughly
**$420K/year on AWS compute** by upgrading; a large enterprise on the
same gap saves **~$5.6M/year**.[^thenewstack] These are numbers any CFO
will sign off on — the blocker isn't ROI, it's *risk* and *labor*.

## The market is real and growing fast

The legacy modernization market is in the **$25–30B range in 2025**
across five major research firms, with **CAGRs of 16–17%** taking it to
**$66–90B+ by 2031–2034**.[^business-research][^skyquest][^mordor]

- Application modernization services alone approach **$30B by 2026**.
- Drivers cited consistently: rising maintenance costs, cybersecurity on
  outdated platforms, cloud migration demand, and the need to plug
  modern systems into legacy data.

This is *not* a category looking for a problem. Every enterprise has at
least one Python 2 holdout, one un-typed monolith, or one
"do-not-touch" service that hasn't seen a dependency upgrade in years.

## Why current AI agents struggle with this work specifically

Refactoring is the use case where developer trust in AI is lowest, and
the empirical results explain why:

- **21.9% of developers explicitly avoid using AI for refactoring
  tasks** — citing concerns about preserving behavior.[^thoughtworks]
- A recent empirical study of agentic refactoring found that **45.1% of
  autonomously generated pull requests required post-review
  fixes** — most often for bugs, refactoring, or
  documentation.[^arxiv-refactor]

The throughline: an agent that "just refactors" produces a diff that
*looks* right but has subtly altered behavior. There is no test to catch
it (because the project didn't have tests in the first place — that's
why you wanted to modernize it). The reviewer is now in the worst
possible position: a giant diff, no safety net, and pressure to ship.

## Our angle: characterization-first, rubric-graded, one PR at a time

The Managed Agents primitives let us do something simpler agents can't:

**1. Build the safety net before changing anything.**
Phases 0–4 of our pipeline (baseline, Python+uv, lint, characterization
tests, types) produce no behavioral changes. Phase 3 specifically writes
tests that *pin current behavior* — including bugs — so the later
refactor steps have a regression gate. This is the missing ingredient in
the 45% post-review-fix rate above: agents refactor without a test net.

**2. Human-in-the-loop only where it matters.**
The agent calls a custom `ask_human` tool for decisions that can't be
made autonomously: "Is this a bug to preserve or fix later?",
"Should I rename this public API?", "Is this 2,000-line dead module
truly dead?". Everything else — the mechanical edits — runs
unsupervised. This matches the hybrid pattern emerging in industry
practice: humans on the 10% that need strategic judgment, agents on the
90% that doesn't.[^analyticsindia]

**3. One PR per phase, with a checkable rubric.**
Each phase's success criteria are explicit and graded by an independent
model. A reviewer sees a small diff with a clear goal and a clear test
that the goal was met. Compare this to the "agent did 800 things in one
diff" failure mode — which is *why* developers don't trust agents for
refactoring.

**4. Memory across phases, not within them.**
Findings from phase 0 (architecture) inform phase 7 (refactor).
Decisions the human made in phase 3 (preserve this bug) are honored in
phase 7. Without cross-session memory, each phase would re-derive the
same context and re-ask the same questions.

## What this unlocks

The pitch is not "let the AI clean your code." Plenty of tools claim
that.

The pitch is:

> *We turn an 18-month modernization project into a sequence of 8
> bite-sized PRs, each one independently reviewable, each one gated on
> behavior-preservation evidence, and each one cheap to revert. The
> human stays in the loop only on the load-bearing calls.*

For an enterprise sitting on a legacy Python codebase, that's the
difference between "we should modernize this" (perpetual backlog item)
and "we modernize one phase this sprint" (calendar-able work).

The market is paying $30B/year to do this slowly by hand. We're not
proposing to replace that work — we're proposing to make the human
hours land on the 10% that needs judgment.

---

## Sources

[^stripe]: Stripe Developer Coefficient — cited in industry analyses
showing ~33% of developer time spent on technical debt and bad code.
[Bitvea — *True Cost of Technical Debt: 5 Hidden Drains
(Quantified)*](https://bitvea.com/en/blog/true-cost-of-technical-debt)

[^bitvea]: Range of 20–42% across research sources, including Stripe's
Developer Coefficient (~33%).
[Bitvea](https://bitvea.com/en/blog/true-cost-of-technical-debt)

[^mckinsey-via-devico]: McKinsey: 30% of CIOs say >20% of new-product
budget is diverted to debt-related work.
[Devico — *The True Cost of Technical
Debt*](https://devico.io/blog/the-true-cost-of-technical-debt-a-financial-analysis)

[^unqork]: Up to 75% of tech budget on legacy maintenance in extreme
cases.
[Unqork — *The True Costs of Maintaining Legacy
Systems*](https://unqork.com/resource-center/blogs/the-true-costs-of-maintaining-legacy-systems/)

[^thenewstack]: AWS savings from Python version upgrades: $420K/year
mid-market, ~$5.6M/year large enterprise.
[The New Stack — *Outdated Python Versions Cost Companies
Millions*](https://thenewstack.io/outdated-python-versions-cost-companies-millions/)

[^business-research]: 2026 market $17.57B at 16.1% CAGR.
[The Business Research Company — *Legacy Software Modernization Market
Trends Report
2026*](https://www.thebusinessresearchcompany.com/report/legacy-software-modernization-global-market-report)

[^skyquest]: Application modernization services ≈ $30B by 2026.
[SkyQuest — *Application Modernization Services Market
Report*](https://www.skyquestt.com/report/application-modernization-services-market)

[^mordor]: Mordor Intelligence valuation at $29.39B in 2026, 17.64%
CAGR.
[Mordor Intelligence — *Legacy Modernization Market Size, Share &
Industry Growth Report,
2031*](https://www.mordorintelligence.com/industry-reports/legacy-modernization-market)

[^thoughtworks]: 21.9% of developers avoid AI for refactoring tasks.
[Thoughtworks — *Cybernetics and the "human-on-the-loop" in agentic
coding*](https://www.thoughtworks.com/en-us/insights/blog/generative-ai/cybernetics-and-human-on-the-loop-in-agentic-coding)

[^arxiv-refactor]: 45.1% of autonomously generated PRs required
post-review fixes.
[*Agentic Refactoring: An Empirical Study of AI Coding Agents* — arXiv
2511.04824](https://arxiv.org/pdf/2511.04824)

[^analyticsindia]: The emerging hybrid pattern: human on the 10%
strategic decisions, agent on the 90% mechanical.
[Analytics India Magazine — *Human-in-the-Loop Is Out, Agent-in-the-Loop
Is In*](https://analyticsindiamag.com/ai-highlights/human-in-the-loop-is-out-agent-in-the-loop-is-in/)
