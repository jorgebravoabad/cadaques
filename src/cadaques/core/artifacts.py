"""Artifact references: large outputs live outside the event log.

An :class:`ArtifactRef` is a small, serializable pointer — content
hash, media type, size, provenance — to data produced by a campaign
(spectra, checkpoints, model dumps). The event log stores references,
never payloads (ADR-0004); :func:`store_artifact` provides the local
content-addressed store the strategy starts with (cloud backends wait
for a real demand).
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class ArtifactRef:
    """A content-addressed reference to an external data product."""

    sha256: str
    uri: str
    media_type: str = "application/octet-stream"
    size_bytes: int = 0
    parents: tuple[str, ...] = ()          # sha256 of inputs, for lineage
    metadata: Mapping[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "sha256": self.sha256,
            "uri": self.uri,
            "media_type": self.media_type,
            "size_bytes": self.size_bytes,
            "parents": list(self.parents),
            "metadata": dict(self.metadata),
        }


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def store_artifact(
    source: bytes | str | Path,
    root: str | Path,
    *,
    media_type: str = "application/octet-stream",
    parents: tuple[str, ...] = (),
    **metadata: str,
) -> ArtifactRef:
    """Store bytes or a file into a local content-addressed tree.

    Layout: ``root/<sha[:2]>/<sha>`` — idempotent by construction:
    storing identical content twice yields the same reference and
    writes nothing new.
    """
    root = Path(root)
    if isinstance(source, (str, Path)):
        payload = Path(source).read_bytes()
    else:
        payload = source
    sha = _digest(payload)
    target = root / sha[:2] / sha
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(source, (str, Path)):
            shutil.copyfile(source, target)
        else:
            target.write_bytes(payload)
    return ArtifactRef(
        sha256=sha,
        uri=target.as_uri(),
        media_type=media_type,
        size_bytes=len(payload),
        parents=parents,
        metadata=metadata,
    )
