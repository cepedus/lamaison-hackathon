"""Project cleaner — staged, rubric-graded refactor of legacy Python projects.

The cleaner runs a target repository through an ordered pipeline of phases,
each implemented as a Managed Agent with its own system prompt and outcome
rubric. Each phase opens one PR and is gated on green CI before the next
phase begins. Findings (architecture notes, flagged bugs, intentional quirks)
accumulate in a memory store shared across phases.

See ``phases.PHASES`` for the canonical phase order and rubric definitions,
and ``orchestrator.Orchestrator`` for the run loop.
"""

from cleaner.orchestrator import Orchestrator
from cleaner.phases import PHASES, Phase

__all__ = ["Orchestrator", "Phase", "PHASES"]
