"""The workspace: campaigns that outlive the session that started them.

Every campaign lives in its own directory under a workspace root::

    <root>/<campaign_id>/spec.json         the declaration (cadaques.spec/1)
    <root>/<campaign_id>/events.jsonl      the record (cadaques.events/2)
    <root>/<campaign_id>/checkpoint.json   participant rng states
    <root>/<campaign_id>/snapshot.json     derived state, a cache (ADR-0015)
    <root>/<campaign_id>/requests.jsonl    idempotency journal

The event log is the only source of truth (ADR-0004). Each mutating
tool call takes the campaign's lock, obtains a :class:`Session` (the
campaign rebuilt from the record, or the one a previous call in this
process left behind, validated against the log and folded forward
over any events appended since), acts, and commits by appending the
new events to ``events.jsonl`` in one fsynced write. A process,
session or agent can therefore die between any two calls without
losing or duplicating anything the record has accepted; a torn final
line is not a committed event and is ignored (and truncated by the
next writer).

Nothing else is trusted: the in-process session and ``snapshot.json``
are both validated against the log (byte offset plus the bytes of the
last event) on every use, and any mismatch falls back to a full
replay from the log (ADR-0015).

The request journal maps each mutating ``request_id`` to the hash of
its arguments and the response sent. It is written after the commit;
if a crash falls in between, the events still carry the request_id and
the tool layer reconstructs the response from them (ADR-0013).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator, Mapping

from ..core.events import Event, EventLog, event_line, read_events_from
from ..runtime.replay import (
    _rng_state,
    apply_event,
    restore,
    restore_rng,
    write_rng_state,
)
from ..runtime.state import CampaignState, StateFold
from .errors import ToolError

try:  # POSIX advisory locks; on other platforms locking is best-effort
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

if TYPE_CHECKING:  # pragma: no cover
    from ..runtime.campaign import Campaign

REQUEST_SCHEMA: str = "cadaques.requests/1"
SNAPSHOT_SCHEMA: str = "cadaques.snapshot/1"
#: Lock-free attempts a read-only call makes before it reads under a shared lock.
READ_RETRIES: int = 4

_REQUEST_ID = re.compile(rb'^\{"schema": "[^"]*", "request_id": ("(?:[^"\\]|\\.)*")')


def _journal_request_id(line: bytes) -> str | None:
    """The request_id of a journal line, or None if the line is not an entry.

    The id is read with a prefix pattern, not a full parse, to keep the index
    cheap; a line that does not look like a complete entry (it must end in
    ``}``) is a torn append that a later writer terminated, and is skipped.
    """
    if not line.rstrip().endswith(b"}"):
        return None
    try:
        m = _REQUEST_ID.match(line)
        return json.loads(m.group(1)) if m else json.loads(line).get("request_id")
    except (ValueError, AttributeError):
        return None


def args_digest(tool: str, args: Mapping[str, Any]) -> str:
    """Canonical hash of a tool call, for idempotency checks."""
    canonical = json.dumps({"tool": tool, "args": args}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class _Stale(Exception):
    """A cached session or snapshot no longer matches the log."""


def _stamp(path: Path) -> tuple[int, int, int] | None:
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    return (st.st_mtime_ns, st.st_size, st.st_ino)


def _line_at(path: Path, end: int, length: int) -> bytes:
    """The ``length`` bytes of ``path`` that end at byte ``end``."""
    with path.open("rb") as fh:
        fh.seek(end - length)
        return fh.read(length)


@dataclass
class Session:
    """One campaign as a tool call sees it: the live object plus what is derived from it.

    ``fold`` and the lookup tables are brought forward lazily from
    ``campaign.events`` (:meth:`sync`), so a handler pays for the events
    it appended, never for the whole log. A session with ``full=False`` is
    a read-only view rebuilt from ``snapshot.json``: its campaign is a shell
    (no history), enough for status and listings.
    """

    campaign_id: str
    campaign: "Campaign"
    fold: StateFold = field(default_factory=StateFold)
    full: bool = True
    #: Bytes of ``events.jsonl`` this session accounts for (a commit boundary).
    offset: int = 0
    #: Raw bytes of the last event of that prefix (for validation).
    last_line: bytes = b""
    #: Number of events on disk that this session accounts for.
    persisted: int = 0
    rng_stamp: tuple[int, int, int] | None = None
    reservations: dict[str, Event] = field(default_factory=dict)
    closings: dict[str, Event] = field(default_factory=dict)
    by_request: dict[str, list[Event]] = field(default_factory=dict)
    _synced: int = 0
    _mark: tuple[Any, ...] | None = field(default=None, repr=False)

    # -- derived state ------------------------------------------------
    def sync(self) -> None:
        if not self.full:
            return
        events = self.campaign.events.events
        for event in events[self._synced:] if self._synced < len(events) else ():
            self.fold.feed(event)
            kind = event.kind
            if kind == "evaluation_reserved":
                self.reservations.setdefault(event.payload["evaluation_id"], event)
            elif kind in ("oracle_result", "oracle_failure", "evaluation_cancelled"):
                eid = event.payload.get("evaluation_id")
                if eid is not None:
                    self.closings.setdefault(eid, event)
            if event.request_id is not None:
                self.by_request.setdefault(event.request_id, []).append(event)
        self._synced = len(events)

    def state(self) -> CampaignState:
        self.sync()
        return self.fold.state()

    @property
    def n_events(self) -> int:
        self.sync()
        return self.fold.n_events

    def pending(self) -> list[Event]:
        """Reservation events not yet settled or cancelled, in order."""
        self.sync()
        return list(self.fold.open.values())

    def reservation(self, evaluation_id: str) -> Event | None:
        self.sync()
        return self.reservations.get(evaluation_id)

    def closing(self, evaluation_id: str) -> Event | None:
        self.sync()
        return self.closings.get(evaluation_id)

    def events_for(self, request_id: str) -> list[Event]:
        self.sync()
        return list(self.by_request.get(request_id, ()))

    # -- cache hygiene ------------------------------------------------
    def _fingerprint(self) -> tuple[Any, ...]:
        c = self.campaign
        return (len(c.events), c.budget.spent, _rng_state(c.driver), _rng_state(c.oracle))

    def mark(self) -> None:
        self._mark = self._fingerprint()

    def dirty(self) -> bool:
        """Has the live campaign moved without the record having committed it?"""
        return self._mark != self._fingerprint()


class _JournalIndex:
    """request_id -> byte span of its line in ``requests.jsonl``, built incrementally."""

    def __init__(self) -> None:
        self.offset = 0
        self.last = b""
        self.spans: dict[str, tuple[int, int]] = {}


class CampaignStore:
    """File-backed campaigns under one workspace root."""

    def __init__(self, root: str | Path, *, lock_timeout: float = 30.0) -> None:
        self.root = Path(root)
        self.lock_timeout = lock_timeout
        self._cache: dict[str, Session] = {}
        self._journals: dict[str, _JournalIndex] = {}

    # -- layout ----------------------------------------------------------
    def path(self, campaign_id: str) -> Path:
        return self.root / campaign_id

    def exists(self, campaign_id: str) -> bool:
        return (self.path(campaign_id) / "events.jsonl").exists()

    def campaign_ids(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(p.name for p in self.root.iterdir() if (p / "events.jsonl").exists())

    # -- locking -----------------------------------------------------------
    @contextmanager
    def lock(self, campaign_id: str, *, shared: bool = False) -> Iterator[None]:
        """Serialize mutations of one campaign across processes.

        ``shared=True`` takes the lock in read mode: it waits for a writer to
        finish, and writers wait for it, but readers do not exclude each other.
        """
        lock_dir = self.root / ".locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        with (lock_dir / f"{campaign_id}.lock").open("a") as fh:
            if fcntl is None:  # pragma: no cover
                yield
                return
            deadline = time.monotonic() + self.lock_timeout
            while True:
                try:
                    fcntl.flock(fh.fileno(), (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ToolError(
                            "busy",
                            f"Campaign {campaign_id!r} is locked by another call; retry shortly.",
                            details={"campaign_id": campaign_id},
                        ) from None
                    time.sleep(0.05)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    # -- campaigns ------------------------------------------------------------
    def load(self, campaign_id: str) -> "Campaign":
        """Rebuild a campaign from the log alone (no cache, no snapshot)."""
        return self._cold(campaign_id).campaign

    def create(self, campaign_id: str, campaign: "Campaign") -> Session:
        """A session for a campaign that has no record yet."""
        session = Session(campaign_id, campaign)
        session.mark()
        return session

    def session(self, campaign_id: str, *, view: bool = False) -> Session:
        """The campaign as the record has it now.

        ``view=True`` accepts a read-only shell rebuilt from the snapshot
        (status, listings); otherwise the session carries the live campaign.
        Every cached or snapshotted form is validated against the log and
        folded forward over newer events; on any mismatch the log is
        replayed in full.
        """
        if not self.exists(campaign_id):
            raise ToolError(
                "unknown_campaign",
                f"No campaign {campaign_id!r} in workspace {str(self.root)!r}.",
                details={"campaign_id": campaign_id},
            )
        cached = self._cache.pop(campaign_id, None)  # checkout: one holder at a time
        if cached is not None:
            try:
                self._advance(campaign_id, cached)
                return cached
            except Exception:  # noqa: BLE001 — a cache that cannot be proved is dropped
                pass
        if view:
            try:
                return self._from_snapshot(campaign_id)
            except Exception:  # noqa: BLE001 — a snapshot that cannot be proved is ignored
                pass
        return self._cold(campaign_id)

    def read_session(self, campaign_id: str, *, view: bool = False) -> Session:
        """:meth:`session` for a caller that does not hold the campaign's lock.

        A lock-free read can overlap a writer (or a writer of an older release
        that rewrote a file in place) and see a torn file. The read is retried
        a few times with a short back-off; if it still fails it is made once
        more holding the lock in shared mode, which waits for any writer to
        finish. A read-only tool therefore never reports an internal error for
        a campaign that is merely being written; a campaign that is really
        damaged still fails, from the locked read.
        """
        for attempt in range(READ_RETRIES):
            try:
                return self.session(campaign_id, view=view)
            except ToolError:
                raise
            except Exception:  # noqa: BLE001 — probably a torn read; try again
                time.sleep(0.002 * 2 ** attempt)
        with self.lock(campaign_id, shared=True):
            return self.session(campaign_id, view=view)

    def release(self, session: Session) -> None:
        """Hand a session back for reuse by the next call, if nothing is uncommitted in it."""
        if session.full and not session.dirty():
            self._cache[session.campaign_id] = session

    # -- reading ------------------------------------------------------------
    def _cold(self, campaign_id: str) -> Session:
        directory = self.path(campaign_id)
        events, offset, last = read_events_from(directory / "events.jsonl", 0)
        campaign = restore(directory, EventLog(events=events))
        session = Session(
            campaign_id, campaign, offset=offset, last_line=last, persisted=len(events),
            rng_stamp=_stamp(directory / "checkpoint.json"),
        )
        session.sync()
        session.mark()
        return session

    def _advance(self, campaign_id: str, session: Session) -> None:
        """Validate a live session against the log; fold in what was appended since."""
        directory = self.path(campaign_id)
        path = directory / "events.jsonl"
        size = path.stat().st_size
        if size < session.offset or (
            session.last_line and _line_at(path, session.offset, len(session.last_line)) != session.last_line
        ):
            raise _Stale
        new, end, last = read_events_from(path, session.offset, session.persisted)
        campaign = session.campaign
        for event in new:
            campaign.events.events.append(event)
            apply_event(campaign, event)
        if new:
            session.offset, session.last_line, session.persisted = end, last, session.persisted + len(new)
        stamp = _stamp(directory / "checkpoint.json")
        if new or stamp != session.rng_stamp:
            restore_rng(campaign, directory)  # another process moved the streams
            session.rng_stamp = stamp
        session.sync()
        session.mark()

    # -- snapshot (derived state; a cache of the log, never the record) --------
    def _write_snapshot(self, campaign_id: str, session: Session) -> None:
        directory = self.path(campaign_id)
        try:
            spec_sha = hashlib.sha256((directory / "spec.json").read_bytes()).hexdigest()
            doc = {
                "schema": SNAPSHOT_SCHEMA,
                "n_events": session.persisted,
                "offset": session.offset,
                "last_line_len": len(session.last_line),
                "last_line_sha256": hashlib.sha256(session.last_line).hexdigest(),
                "spec_sha256": spec_sha,
                "fold": session.fold.to_dict(),
            }
            tmp = directory / "snapshot.json.tmp"
            tmp.write_text(json.dumps(doc), encoding="utf-8")
            os.replace(tmp, directory / "snapshot.json")  # no fsync: losing it only costs a replay
        except OSError:  # pragma: no cover — a cache that cannot be written is just absent
            pass

    def _from_snapshot(self, campaign_id: str) -> Session:
        from ..core.spec import CampaignSpec

        directory = self.path(campaign_id)
        doc = json.loads((directory / "snapshot.json").read_text(encoding="utf-8"))
        if doc.get("schema") != SNAPSHOT_SCHEMA:
            raise _Stale
        spec_bytes = (directory / "spec.json").read_bytes()
        if hashlib.sha256(spec_bytes).hexdigest() != doc["spec_sha256"]:
            raise _Stale
        path = directory / "events.jsonl"
        offset, length = int(doc["offset"]), int(doc["last_line_len"])
        if path.stat().st_size < offset or length > offset:
            raise _Stale
        last = _line_at(path, offset, length)
        if hashlib.sha256(last).hexdigest() != doc["last_line_sha256"]:
            raise _Stale
        fold = StateFold.from_dict(doc["fold"])
        if fold.n_events != int(doc["n_events"]):
            raise _Stale
        new, end, tail_last = read_events_from(path, offset, fold.n_events)
        for event in new:
            fold.feed(event)
        campaign = CampaignSpec.loads(spec_bytes.decode("utf-8")).build()
        campaign.budget.charge(fold.spent, settle=True)
        return Session(
            campaign_id, campaign, fold=fold, full=False, offset=end,
            last_line=tail_last or last, persisted=fold.n_events,
        )

    # -- writing ------------------------------------------------------------
    def save(self, session: Session) -> None:
        """Commit a session: rng state first, then the new events (the commit point).

        Called with the campaign's lock held. The events appended since the
        session was obtained go to the end of ``events.jsonl`` in one fsynced
        write; a new campaign's log is created atomically.
        """
        if not session.full:  # pragma: no cover — views are read-only
            raise ValueError("A snapshot view cannot be saved.")
        session.sync()
        campaign_id = session.campaign_id
        directory = self.path(campaign_id)
        directory.mkdir(parents=True, exist_ok=True)
        campaign = session.campaign
        if not (directory / "spec.json").exists():
            campaign.to_spec().dump(directory / "spec.json")
        write_rng_state(campaign, directory)
        path = directory / "events.jsonl"
        new = campaign.events.events[session.persisted:]
        if session.persisted == 0:
            campaign.events.to_jsonl(path)
            lines = [event_line(e) for e in campaign.events.events[-1:]]
            session.offset = path.stat().st_size
        elif new:
            lines = [event_line(e) for e in new]
            size = path.stat().st_size
            if size < session.offset:
                raise ToolError("internal_error", "The event log shrank under a held session.")
            with path.open("r+b") as fh:
                if size > session.offset:
                    fh.truncate(session.offset)  # a torn append, never committed
                fh.seek(session.offset)
                data = b"".join(lines)
                if session.last_line and not session.last_line.endswith(b"\n"):
                    data = b"\n" + data
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            session.offset += len(data)
        else:
            lines = []
        if lines:
            session.last_line = lines[-1]
        session.persisted = len(campaign.events.events)
        session.rng_stamp = _stamp(directory / "checkpoint.json")
        session.mark()
        self._write_snapshot(campaign_id, session)
        self._cache[campaign_id] = session

    # -- idempotency journal -------------------------------------------------
    def _journal_index(self, campaign_id: str, path: Path) -> _JournalIndex:
        index = self._journals.get(campaign_id)
        size = path.stat().st_size
        if index is not None and (
            size < index.offset
            or (index.last and _line_at(path, index.offset, len(index.last)) != index.last)
        ):
            index = None
        if index is None:
            index = self._journals[campaign_id] = _JournalIndex()
        if size > index.offset:
            with path.open("rb") as fh:
                fh.seek(index.offset)
                data = fh.read()
            pos = 0
            while pos < len(data):
                nl = data.find(b"\n", pos)
                if nl == -1:
                    break  # torn append: not an entry; journal_append repairs it
                line = data[pos:nl + 1]
                if line.strip():
                    request_id = _journal_request_id(line)
                    if request_id is not None:  # else: a torn line that a newline later closed
                        index.spans.setdefault(request_id, (index.offset + pos, index.offset + nl + 1))
                index.last = line
                pos = nl + 1
            index.offset += pos
        return index

    def journal_lookup(self, campaign_id: str, request_id: str) -> dict[str, Any] | None:
        path = self.path(campaign_id) / "requests.jsonl"
        if not path.exists():
            return None
        span = self._journal_index(campaign_id, path).spans.get(request_id)
        if span is None:
            return None
        with path.open("rb") as fh:
            fh.seek(span[0])
            raw = fh.read(span[1] - span[0])
        try:
            return json.loads(raw)
        except ValueError:  # a damaged line is not an answer; the events can still reconstruct one
            return None

    def journal_append(
        self, campaign_id: str, request_id: str, tool: str, digest: str, response: Mapping[str, Any]
    ) -> None:
        entry = {
            "schema": REQUEST_SCHEMA,
            "request_id": request_id,
            "tool": tool,
            "args_sha256": digest,
            "timestamp": time.time(),
            "response": dict(response),
        }
        path = self.path(campaign_id) / "requests.jsonl"
        if path.exists():
            self._repair_journal_tail(campaign_id, path)
        with path.open("ab") as fh:
            fh.write((json.dumps(entry) + "\n").encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())

    def _repair_journal_tail(self, campaign_id: str, path: Path) -> None:
        """Make the journal end on a line boundary before appending (lock held).

        A crash can leave a final fragment without its newline. If it is a
        whole entry that only lacks the newline, the newline is added; if it is
        torn, it is cut off (it was never an answer, and the events carry the
        request_id for reconstruction). Either way the next entry never gets
        glued onto it.
        """
        index = self._journal_index(campaign_id, path)
        size = path.stat().st_size
        if size == index.offset:
            return
        with path.open("r+b") as fh:
            fh.seek(index.offset)
            fragment = fh.read()
            try:
                json.loads(fragment)
            except ValueError:
                fh.truncate(index.offset)
            else:
                fh.seek(0, os.SEEK_END)
                fh.write(b"\n")
            fh.flush()
            os.fsync(fh.fileno())
