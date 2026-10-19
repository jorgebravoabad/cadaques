"""The campaign event log: the source of truth (ADR-0004).

Every meaningful transition of a campaign — start, driver proposal,
rejection, reservation, oracle result, failure, cancellation, stop — is
an immutable :class:`Event` appended to an :class:`EventLog`. State,
the accounting :class:`~cadaques.core.ledger.Ledger`, checkpoints and
the final outcome are *derived* from events; no component may hold
state the events cannot reconstruct.

Serialization is JSONL with a schema-version field on every line
(ADR-0005). Schema 2 (ADR-0014) adds the two reservation kinds and two
attribution fields on every event: ``actor``, who caused it (a person,
or an agent identified by model and version), and ``request_id``, the
tool call that caused it. Schema-1 logs still load; their events carry
``actor=None`` and ``request_id=None``.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping

#: Schema identifier written on every serialized event line.
EVENT_SCHEMA: str = "cadaques.events/2"

#: Schemas this release can read.
READABLE_EVENT_SCHEMAS: tuple[str, ...] = ("cadaques.events/1", EVENT_SCHEMA)

#: Closed vocabulary of event kinds in schema version 2.
EVENT_KINDS: tuple[str, ...] = (
    "campaign_started",
    "driver_proposal",
    "rejected",
    "evaluation_reserved",
    "oracle_result",
    "oracle_failure",
    "evaluation_cancelled",
    "stopped",
)


@dataclass(frozen=True)
class Event:
    """One immutable transition of a campaign."""

    seq: int
    kind: str
    payload: Mapping[str, Any]
    timestamp: float = field(default_factory=time.time)
    actor: Mapping[str, Any] | None = None
    request_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": EVENT_SCHEMA,
            "seq": self.seq,
            "kind": self.kind,
            "timestamp": self.timestamp,
            "actor": dict(self.actor) if self.actor is not None else None,
            "request_id": self.request_id,
            "payload": dict(self.payload),
        }


@dataclass
class EventLog:
    """Append-only, sequence-numbered log of campaign events."""

    events: list[Event] = field(default_factory=list)
    _actor: Mapping[str, Any] | None = field(default=None, repr=False, compare=False)
    _request_id: str | None = field(default=None, repr=False, compare=False)

    def append(self, kind: str, **payload: Any) -> Event:
        if kind not in EVENT_KINDS:
            raise ValueError(f"Unknown event kind {kind!r}; schema {EVENT_SCHEMA}")
        event = Event(
            seq=len(self.events),
            kind=kind,
            payload=payload,
            actor=self._actor,
            request_id=self._request_id,
        )
        self.events.append(event)
        return event

    @contextmanager
    def acting_as(
        self, actor: Mapping[str, Any] | None, request_id: str | None = None
    ) -> Iterator["EventLog"]:
        """Attribute every event appended inside the block (ADR-0014)."""
        previous = (self._actor, self._request_id)
        self._actor = dict(actor) if actor is not None else None
        self._request_id = request_id
        try:
            yield self
        finally:
            self._actor, self._request_id = previous

    def __iter__(self) -> Iterator[Event]:
        return iter(self.events)

    def __len__(self) -> int:
        return len(self.events)

    def of_kind(self, kind: str) -> list[Event]:
        return [e for e in self.events if e.kind == kind]

    def by_request(self, request_id: str) -> list[Event]:
        return [e for e in self.events if e.request_id == request_id]

    # -- persistence --------------------------------------------------
    def to_jsonl(self, path: str | Path) -> Path:
        """Write the log atomically: a crash leaves the old file or the new one."""
        path = Path(path)
        tmp = path.with_name(path.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for event in self.events:
                fh.write(json.dumps(event.as_dict()) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        return path

    @classmethod
    def from_jsonl(cls, path: str | Path) -> "EventLog":
        log = cls()
        with Path(path).open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                raw = json.loads(line)
                schema = raw.get("schema")
                if schema not in READABLE_EVENT_SCHEMAS:
                    raise ValueError(
                        f"Unsupported event schema {schema!r} "
                        f"(readable: {', '.join(READABLE_EVENT_SCHEMAS)})"
                    )
                log.events.append(
                    Event(
                        seq=int(raw["seq"]),
                        kind=str(raw["kind"]),
                        payload=dict(raw["payload"]),
                        timestamp=float(raw["timestamp"]),
                        actor=raw.get("actor"),
                        request_id=raw.get("request_id"),
                    )
                )
        for expected, event in enumerate(log.events):
            if event.seq != expected:
                raise ValueError(
                    f"Event log corrupt: seq {event.seq} at position {expected}"
                )
        return log
