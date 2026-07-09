"""The campaign ledger: every transaction, declared and settled.

The ledger is the accounting record of a discovery campaign and,
almost for free, its provenance and replay substrate: exporting it
to JSONL yields a complete, reproducible trace of what was asked,
what it was expected to cost, and what it actually cost.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Literal, Mapping

from .cost import Cost

TransactionKind = Literal["oracle", "driver"]


@dataclass(frozen=True)
class Transaction:
    kind: TransactionKind
    label: str
    declared: Cost
    settled: Cost
    timestamp: float = field(default_factory=time.time)
    meta: Mapping[str, Any] = field(default_factory=dict)

    @property
    def overrun(self) -> Cost:
        """Settled minus declared: the price of optimism."""
        return self.settled - self.declared


@dataclass
class Ledger:
    """Append-only record of all campaign transactions."""

    transactions: list[Transaction] = field(default_factory=list)

    def record(
        self,
        kind: TransactionKind,
        label: str,
        declared: Cost,
        settled: Cost,
        **meta: Any,
    ) -> Transaction:
        tx = Transaction(kind=kind, label=label, declared=declared, settled=settled, meta=meta)
        self.transactions.append(tx)
        return tx

    # -- aggregation ------------------------------------------------
    def total(self, kind: TransactionKind | None = None) -> Cost:
        total = Cost()
        for tx in self.transactions:
            if kind is None or tx.kind == kind:
                total = total + tx.settled
        return total

    def cumulative(self, kind: TransactionKind | None = None) -> Iterator[tuple[Transaction, Cost]]:
        """Yield (transaction, running settled total) pairs."""
        running = Cost()
        for tx in self.transactions:
            if kind is None or tx.kind == kind:
                running = running + tx.settled
                yield tx, running

    def __len__(self) -> int:
        return len(self.transactions)

    # -- persistence ------------------------------------------------
    def to_jsonl(self, path: str | Path) -> Path:
        path = Path(path)
        with path.open("w", encoding="utf-8") as fh:
            for tx in self.transactions:
                fh.write(
                    json.dumps(
                        {
                            "kind": tx.kind,
                            "label": tx.label,
                            "declared": tx.declared.as_dict(),
                            "settled": tx.settled.as_dict(),
                            "timestamp": tx.timestamp,
                            "meta": dict(tx.meta),
                        }
                    )
                    + "\n"
                )
        return path
