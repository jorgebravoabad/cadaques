"""The :class:`ExternalDriver`: the operator proposes, not the kernel.

In an agent-operated campaign the next point may come from the agent
(or person) operating the campaign rather than from an in-process
strategy. The spec still names a driver — every campaign has one — and
this is it: it declines to propose, so every query must arrive with
explicit parameters through ``reserve_evaluation``. The operator's
deliberation is still metered: it reports its decision cost (tokens,
wall time) on the reservation and the kernel charges it to the same
budget as any other driver (ADR-0006, metered intelligence).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..core.cost import BudgetView
from ..core.records import Query, Result


class ExternalProposal(RuntimeError):
    """An ExternalDriver was asked to propose in-process."""


@dataclass
class ExternalDriver:
    """Placeholder driver for campaigns whose operator chooses each query."""

    def propose(self, history: Sequence[Result], budget: BudgetView) -> Query:
        raise ExternalProposal(
            "This campaign's driver is external: pass explicit params to "
            "reserve_evaluation."
        )

    def observe(self, result: Result) -> None:
        pass
