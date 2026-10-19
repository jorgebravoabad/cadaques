"""The workspace: campaigns that outlive the session that started them.

Every campaign lives in its own directory under a workspace root::

    <root>/<campaign_id>/spec.json         the declaration (cadaques.spec/1)
    <root>/<campaign_id>/events.jsonl      the record (cadaques.events/2)
    <root>/<campaign_id>/checkpoint.json   participant rng states
    <root>/<campaign_id>/requests.jsonl    idempotency journal

The agent layer holds no campaign in memory between calls. Each tool
call takes the campaign's lock, rebuilds it from disk with
:func:`~cadaques.runtime.replay.resume`, acts, and writes it back with
:func:`~cadaques.runtime.replay.checkpoint`, whose final, atomic write
of the event log is the commit point. A process, session or agent can
therefore die between any two calls without losing or duplicating
anything the record has accepted.

The request journal maps each mutating ``request_id`` to the hash of
its arguments and the response sent. It is written after the commit;
if a crash falls in between, the events still carry the request_id and
the tool layer reconstructs the response from them (ADR-0013).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator, Mapping

from ..runtime.replay import checkpoint, resume
from .errors import ToolError

try:  # POSIX advisory locks; on other platforms locking is best-effort
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

if TYPE_CHECKING:  # pragma: no cover
    from ..runtime.campaign import Campaign

REQUEST_SCHEMA: str = "cadaques.requests/1"


def args_digest(tool: str, args: Mapping[str, Any]) -> str:
    """Canonical hash of a tool call, for idempotency checks."""
    canonical = json.dumps({"tool": tool, "args": args}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class CampaignStore:
    """File-backed campaigns under one workspace root."""

    def __init__(self, root: str | Path, *, lock_timeout: float = 30.0) -> None:
        self.root = Path(root)
        self.lock_timeout = lock_timeout

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
    def lock(self, campaign_id: str) -> Iterator[None]:
        """Serialize mutations of one campaign across processes."""
        lock_dir = self.root / ".locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        with (lock_dir / f"{campaign_id}.lock").open("a") as fh:
            if fcntl is None:  # pragma: no cover
                yield
                return
            deadline = time.monotonic() + self.lock_timeout
            while True:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
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
        if not self.exists(campaign_id):
            raise ToolError(
                "unknown_campaign",
                f"No campaign {campaign_id!r} in workspace {str(self.root)!r}.",
                details={"campaign_id": campaign_id},
            )
        return resume(self.path(campaign_id))

    def save(self, campaign_id: str, campaign: "Campaign") -> None:
        checkpoint(campaign, self.path(campaign_id))

    # -- idempotency journal -------------------------------------------------
    def journal_lookup(self, campaign_id: str, request_id: str) -> dict[str, Any] | None:
        path = self.path(campaign_id) / "requests.jsonl"
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    entry = json.loads(line)
                    if entry.get("request_id") == request_id:
                        return entry
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
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
