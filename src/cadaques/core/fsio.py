"""Durable file writes shared by the record's small files.

``spec.json``, ``checkpoint.json`` and the snapshot are replaced, never
edited in place: the new content goes to a temporary sibling, is flushed
(and by default fsynced) and then moved over the old file with
:func:`os.replace`. A crash, or a concurrent reader, therefore sees the
old file or the new one, never a truncated or half-written file.
"""

from __future__ import annotations

import os
from pathlib import Path


def write_atomic(path: str | Path, text: str, *, fsync: bool = True) -> Path:
    """Atomically replace ``path`` with ``text`` (UTF-8)."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        if fsync:
            os.fsync(fh.fileno())
    os.replace(tmp, path)
    return path
