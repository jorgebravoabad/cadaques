"""The :class:`Resource` protocol: operational science (ADR-0003).

The dual architecture, made code. :class:`~cadaques.protocols.oracle.Oracle`
is the lightweight synchronous contract (price/evaluate);
:class:`Resource` is the lifecycle contract operational science needs
— submit, poll, collect, cancel — speaking the general
:class:`~cadaques.core.observation.Action` /
:class:`~cadaques.core.observation.Observation` envelopes.

:class:`OracleResource` wraps any Oracle as a Resource, so the two
worlds compose: a campaign that targets Resources runs every 0.1
oracle unchanged. Asynchronous *executors* (pools, retries, Slurm)
are deliberately absent here: they arrive only after replay and
failure semantics are frozen (ADR-0007 decision gate).
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol, runtime_checkable

from ..core.cost import Cost
from ..core.observation import Action, FailureRecord, Observation, ObservationStatus


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED)


@dataclass(frozen=True)
class JobHandle:
    """A serializable ticket for submitted work."""

    id: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class Resource(Protocol):
    """Operational capability with an explicit job lifecycle."""

    def estimate(self, action: Action) -> Cost:
        """Declared (ex-ante) cost — side-effect free, used for admission."""
        ...

    def submit(self, action: Action) -> JobHandle:
        ...

    def status(self, handle: JobHandle) -> JobStatus:
        ...

    def collect(self, handle: JobHandle) -> Observation:
        """Return the terminal Observation; a handle can be collected once."""
        ...

    def cancel(self, handle: JobHandle) -> None:
        ...


class OracleResource:
    """Adapt any synchronous Oracle to the Resource lifecycle.

    ``submit`` evaluates immediately (the oracle *is* synchronous) and
    parks the terminal Observation; ``status`` is therefore always
    terminal. An oracle exception becomes a FAILED observation that
    settles its declared cost (ADR-0006) — the same semantics the
    campaign loop applies, implemented once here for the Resource
    world.
    """

    def __init__(self, oracle: Any) -> None:
        self.oracle = oracle
        self._counter = itertools.count()
        self._done: dict[str, Observation] = {}

    def estimate(self, action: Action) -> Cost:
        return self.oracle.price(action.as_query())

    def submit(self, action: Action) -> JobHandle:
        query = action.as_query()
        declared = self.oracle.price(query)
        try:
            observation = Observation.from_result(self.oracle.evaluate(query))
        except Exception as exc:  # noqa: BLE001 — failure is a result (ADR-0006)
            observation = Observation(
                action=action,
                status=ObservationStatus.FAILED,
                cost=declared,
                failure=FailureRecord(
                    kind="oracle_error",
                    detail=str(exc),
                    retryable=False,
                    exception=type(exc).__name__,
                ),
            )
        handle = JobHandle(id=f"oracle-{next(self._counter)}")
        self._done[handle.id] = observation
        return handle

    def status(self, handle: JobHandle) -> JobStatus:
        obs = self._done.get(handle.id)
        if obs is None:
            raise KeyError(f"Unknown or already-collected handle {handle.id!r}")
        return JobStatus.FAILED if obs.status == ObservationStatus.FAILED else JobStatus.DONE

    def collect(self, handle: JobHandle) -> Observation:
        try:
            return self._done.pop(handle.id)
        except KeyError:
            raise KeyError(f"Unknown or already-collected handle {handle.id!r}") from None

    def cancel(self, handle: JobHandle) -> None:
        self._done.pop(handle.id, None)
