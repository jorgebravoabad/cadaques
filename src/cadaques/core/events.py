"""The campaign event log: the source of truth (ADR-0004).

Every meaningful transition of a campaign — start, driver proposal,
rejection, oracle result, failure, stop — is an immutable
:class:`Event` appended to an :class:`EventLog`. State, the accounting
:class:`~cadaques.core.ledger.Ledger`, checkpoints and the final
outcome are *derived* from events; no component may hold state the
events cannot reconstruct.

Serialization is JSONL with a schema-version field on every line
(ADR-0005: version fields now, migration machinery only when a real
schema v2 exists).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping

#: Schema identifier written on every serialized event line.
EVENT_SCHEMA: str = "cadaques.events/1"

#: Closed vocabulary of event kinds in schema version 1.
EVENT_KINDS: tuple[str, ...] = (
    "campaign_started",
    "driver_proposal",
    "rejected",
    "oracle_result",
    "oracle_failure",
    "stopped",
)


@dataclass(frozen=True)
class Event:
    """One immutable transition of a campaign."""

    seq: int
    kind: str
    payload: Mapping[str, Any]
    timestamp: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": EVENT_SCHEMA,
            "seq": self.seq,
            "kind": self.kind,
            "timestamp": self.timestamp,
            "payload": dict(self.payload),
        }


@dataclass
class EventLog:
    """Append-only, sequence-numbered log of campaign events."""

    events: list[Event] = field(default_factory=list)

    def append(self, kind: str, **payload: Any) -> Event:
        if kind not in EVENT_KINDS:
            raise ValueError(f"Unknown event kind {kind!r}; schema {EVENT_SCHEMA}")
        event = Event(seq=len(self.events), kind=kind, payload=payload)
        self.events.append(event)
        return event

    def __iter__(self) -> Iterator[Event]:
        return iter(self.events)

    def __len__(self) -> int:
        return len(self.events)

    def of_kind(self, kind: str) -> list[Event]:
        return [e for e in self.events if e.kind == kind]

    # -- persistence --------------------------------------------------
    def to_jsonl(self, path: str | Path) -> Path:
        path = Path(path)
        with path.open("w", encoding="utf-8") as fh:
            for event in self.events:
                fh.write(json.dumps(event.as_dict()) + "\n")
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
                if schema != EVENT_SCHEMA:
                    raise ValueError(
                        f"Unsupported event schema {schema!r} (expected {EVENT_SCHEMA!r})"
                    )
                log.events.append(
                    Event(
                        seq=int(raw["seq"]),
                        kind=str(raw["kind"]),
                        payload=dict(raw["payload"]),
                        timestamp=float(raw["timestamp"]),
                    )
                )
        for expected, event in enumerate(log.events):
            if event.seq != expected:
                raise ValueError(
                    f"Event log corrupt: seq {event.seq} at position {expected}"
                )
        return log
