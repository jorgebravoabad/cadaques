"""Derived campaign state: a pure fold over the event log (ADR-0004).

:class:`CampaignState` holds nothing the events cannot reconstruct;
:func:`reduce` is the reducer. The runtime's live outcome and any
after-the-fact replay of a recorded log must agree exactly — that
equivalence is a CI-enforced test, and the synchronous half of the
replay guarantee.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from ..core.cost import Cost
from ..core.events import Event


@dataclass(frozen=True)
class CampaignState:
    """Everything a campaign's events determine about it."""

    started: bool = False
    maximize: bool = True
    n_proposals: int = 0
    n_queries: int = 0
    n_failures: int = 0
    n_rejected: int = 0
    best_value: float | None = None
    best_params: Mapping[str, Any] = field(default_factory=dict)
    spent: Cost = field(default_factory=Cost)
    declared_oracle: Cost = field(default_factory=Cost)
    settled_oracle: Cost = field(default_factory=Cost)
    stop_reason: str | None = None

    @property
    def overrun(self) -> Cost:
        """Settled minus declared oracle cost — the price of optimism,
        as a derived observable of the whole campaign."""
        return self.settled_oracle - self.declared_oracle


def reduce(events: Iterable[Event]) -> CampaignState:
    """Fold an event log into its :class:`CampaignState`."""
    started = False
    maximize = True
    n_proposals = n_queries = n_failures = n_rejected = 0
    best_value: float | None = None
    best_params: dict[str, Any] = {}
    spent = Cost()
    declared_oracle = Cost()
    settled_oracle = Cost()
    stop_reason: str | None = None

    for event in events:
        p = event.payload
        if event.kind == "campaign_started":
            started = True
            maximize = bool(p.get("maximize", True))
        elif event.kind == "driver_proposal":
            n_proposals += 1
            spent = spent + Cost.from_dict(p["settled"])
        elif event.kind == "rejected":
            n_rejected += 1
        elif event.kind == "oracle_result":
            n_queries += 1
            declared_oracle = declared_oracle + Cost.from_dict(p["declared"])
            settled = Cost.from_dict(p["settled"])
            settled_oracle = settled_oracle + settled
            spent = spent + settled
            value = p.get("value")
            if value is not None:
                better = (
                    best_value is None
                    or (value > best_value if maximize else value < best_value)
                )
                if better:
                    best_value = float(value)
                    best_params = dict(p.get("params", {}))
        elif event.kind == "oracle_failure":
            n_queries += 1
            n_failures += 1
            declared_oracle = declared_oracle + Cost.from_dict(p["declared"])
            settled = Cost.from_dict(p["settled"])
            settled_oracle = settled_oracle + settled
            spent = spent + settled
        elif event.kind == "stopped":
            stop_reason = p.get("reason")

    return CampaignState(
        started=started,
        maximize=maximize,
        n_proposals=n_proposals,
        n_queries=n_queries,
        n_failures=n_failures,
        n_rejected=n_rejected,
        best_value=best_value,
        best_params=best_params,
        spent=spent,
        declared_oracle=declared_oracle,
        settled_oracle=settled_oracle,
        stop_reason=stop_reason,
    )
