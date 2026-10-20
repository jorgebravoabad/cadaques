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
    n_cancelled: int = 0
    #: Declared cost of reservations not yet settled or cancelled (ADR-0014).
    reserved: Cost = field(default_factory=Cost)
    #: Evaluation ids reserved but not yet settled or cancelled, in order.
    pending: tuple[str, ...] = ()

    @property
    def overrun(self) -> Cost:
        """Settled minus declared oracle cost — the price of optimism,
        as a derived observable of the whole campaign."""
        return self.settled_oracle - self.declared_oracle


class StateFold:
    """The reducer as an incremental fold (ADR-0004, ADR-0015).

    ``StateFold().feed(e)`` for every event, in order, yields exactly
    :func:`reduce`'s state; feeding only the events appended since is how a
    derived-state snapshot is brought forward without re-reading the log.
    The fold serializes with :meth:`to_dict` so that it can be snapshotted,
    but it is never the record: a snapshot is validated against the log
    before use and discarded when it does not match.
    """

    def __init__(self) -> None:
        self.n_events = 0
        self.started = False
        self.maximize = True
        self.n_proposals = self.n_queries = self.n_failures = self.n_rejected = 0
        self.n_cancelled = 0
        self.best_value: float | None = None
        self.best_params: dict[str, Any] = {}
        self.spent = Cost()
        self.declared_oracle = Cost()
        self.settled_oracle = Cost()
        self.stop_reason: str | None = None
        #: Reservations not yet settled or cancelled, in order of reservation.
        self.open: dict[str, Event] = {}

    def feed(self, event: Event) -> None:
        self.n_events += 1
        p = event.payload
        kind = event.kind
        if kind == "campaign_started":
            self.started = True
            self.maximize = bool(p.get("maximize", True))
        elif kind == "driver_proposal":
            self.n_proposals += 1
            self.spent = self.spent + Cost.from_dict(p["settled"])
        elif kind == "rejected":
            self.n_rejected += 1
        elif kind == "evaluation_reserved":
            self.open[p["evaluation_id"]] = event
        elif kind == "oracle_result":
            self.n_queries += 1
            self._settle(p)
            value = p.get("value")
            if value is not None:
                better = (
                    self.best_value is None
                    or (value > self.best_value if self.maximize else value < self.best_value)
                )
                if better:
                    self.best_value = float(value)
                    self.best_params = dict(p.get("params", {}))
        elif kind == "oracle_failure":
            self.n_queries += 1
            self.n_failures += 1
            self._settle(p)
        elif kind == "evaluation_cancelled":
            self.n_cancelled += 1
            self._settle(p)
        elif kind == "stopped":
            self.stop_reason = p.get("reason")

    def _settle(self, p: Mapping[str, Any]) -> None:
        self.declared_oracle = self.declared_oracle + Cost.from_dict(p["declared"])
        settled = Cost.from_dict(p["settled"])
        self.settled_oracle = self.settled_oracle + settled
        self.spent = self.spent + settled
        evaluation_id = p.get("evaluation_id")
        if evaluation_id is not None:
            self.open.pop(evaluation_id, None)

    def state(self) -> CampaignState:
        return CampaignState(
            started=self.started,
            maximize=self.maximize,
            n_proposals=self.n_proposals,
            n_queries=self.n_queries,
            n_failures=self.n_failures,
            n_rejected=self.n_rejected,
            best_value=self.best_value,
            best_params=dict(self.best_params),
            spent=self.spent,
            declared_oracle=self.declared_oracle,
            settled_oracle=self.settled_oracle,
            stop_reason=self.stop_reason,
            n_cancelled=self.n_cancelled,
            reserved=sum((Cost.from_dict(e.payload["declared"]) for e in self.open.values()), Cost()),
            pending=tuple(self.open),
        )

    # -- snapshot form ------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "n_events": self.n_events, "started": self.started, "maximize": self.maximize,
            "n_proposals": self.n_proposals, "n_queries": self.n_queries,
            "n_failures": self.n_failures, "n_rejected": self.n_rejected,
            "n_cancelled": self.n_cancelled,
            "best_value": self.best_value, "best_params": dict(self.best_params),
            "spent": self.spent.as_dict(), "declared_oracle": self.declared_oracle.as_dict(),
            "settled_oracle": self.settled_oracle.as_dict(), "stop_reason": self.stop_reason,
            "open": [e.as_dict() for e in self.open.values()],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "StateFold":
        from ..core.events import event_from_dict

        fold = cls()
        fold.n_events = int(raw["n_events"])
        fold.started, fold.maximize = bool(raw["started"]), bool(raw["maximize"])
        fold.n_proposals, fold.n_queries = int(raw["n_proposals"]), int(raw["n_queries"])
        fold.n_failures, fold.n_rejected = int(raw["n_failures"]), int(raw["n_rejected"])
        fold.n_cancelled = int(raw["n_cancelled"])
        fold.best_value = raw["best_value"]
        fold.best_params = dict(raw["best_params"])
        fold.spent = Cost.from_dict(raw["spent"])
        fold.declared_oracle = Cost.from_dict(raw["declared_oracle"])
        fold.settled_oracle = Cost.from_dict(raw["settled_oracle"])
        fold.stop_reason = raw["stop_reason"]
        for e in map(event_from_dict, raw["open"]):
            fold.open[e.payload["evaluation_id"]] = e
        return fold


def reduce(events: Iterable[Event]) -> CampaignState:
    """Fold an event log into its :class:`CampaignState`."""
    fold = StateFold()
    for event in events:
        fold.feed(event)
    return fold.state()
