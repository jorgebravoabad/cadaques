"""Cost primitives: the first-class citizens of CADAQUES.

Every oracle query and every driver decision is priced in a
multi-currency :class:`Cost` and charged against a single
:class:`Budget`. Campaigns end when the budget is exhausted,
not when an iteration counter runs out.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterator, Mapping

#: Currencies tracked by the framework. Heterogeneous by design:
#: a DFT relaxation, a beamline measurement and an LLM call are
#: not the same kind of expense.
CURRENCIES: tuple[str, ...] = ("seconds", "cpu_hours", "euros", "tokens")


@dataclass(frozen=True, slots=True)
class Cost:
    """An immutable multi-currency cost vector.

    All components default to zero, so ``Cost(seconds=3.0)`` prices a
    3-second query and ``Cost()`` is the free cost.
    """

    seconds: float = 0.0
    cpu_hours: float = 0.0
    euros: float = 0.0
    tokens: float = 0.0

    # -- arithmetic -------------------------------------------------
    def __add__(self, other: "Cost") -> "Cost":
        return Cost(**{c: getattr(self, c) + getattr(other, c) for c in CURRENCIES})

    def __sub__(self, other: "Cost") -> "Cost":
        return Cost(**{c: getattr(self, c) - getattr(other, c) for c in CURRENCIES})

    def scaled(self, factor: float) -> "Cost":
        return Cost(**{c: getattr(self, c) * factor for c in CURRENCIES})

    # -- queries ----------------------------------------------------
    def items(self) -> Iterator[tuple[str, float]]:
        for c in CURRENCIES:
            yield c, getattr(self, c)

    def as_dict(self) -> dict[str, float]:
        return dict(self.items())

    @property
    def is_free(self) -> bool:
        return all(v == 0 for _, v in self.items())

    def dominated_by(self, other: "Cost") -> bool:
        """True if every component of ``self`` is <= that of ``other``."""
        return all(getattr(self, c) <= getattr(other, c) for c in CURRENCIES)

    @classmethod
    def from_dict(cls, data: Mapping[str, float]) -> "Cost":
        unknown = set(data) - set(CURRENCIES)
        if unknown:
            raise ValueError(f"Unknown cost currencies: {sorted(unknown)}")
        return cls(**{k: float(v) for k, v in data.items()})

    def __repr__(self) -> str:  # compact: only non-zero currencies
        nonzero = ", ".join(f"{c}={v:g}" for c, v in self.items() if v)
        return f"Cost({nonzero or '0'})"


class BudgetExceeded(RuntimeError):
    """Raised when a charge is attempted beyond the campaign budget."""


@dataclass(frozen=True, slots=True)
class BudgetView:
    """A read-only snapshot of the budget, exposed to Drivers.

    Drivers may adapt their strategy to the remaining budget (e.g.
    explore early, exploit when funds run low) but cannot mutate it.
    """

    total: Cost
    spent: Cost

    @property
    def remaining(self) -> Cost:
        return self.total - self.spent

    @property
    def fraction_used(self) -> float:
        """Largest used fraction across the currencies with a limit."""
        fractions = [
            getattr(self.spent, c) / getattr(self.total, c)
            for c in CURRENCIES
            if getattr(self.total, c) > 0
        ]
        return max(fractions, default=0.0)


@dataclass
class Budget:
    """The single budget of a discovery campaign.

    Only currencies with a positive ``total`` component are limited;
    the rest are tracked but unbounded. Declared costs are checked
    with :meth:`can_afford` *before* a query is issued; settled costs
    are charged unconditionally with ``charge(..., settle=True)`` —
    in the real world one discovers an overrun after paying for it,
    and the ledger keeps the evidence.
    """

    total: Cost
    spent: Cost = field(default_factory=Cost)

    def can_afford(self, cost: Cost) -> bool:
        prospective = self.spent + cost
        return all(
            getattr(prospective, c) <= getattr(self.total, c)
            for c in CURRENCIES
            if getattr(self.total, c) > 0
        )

    def charge(self, cost: Cost, *, settle: bool = False) -> None:
        if not settle and not self.can_afford(cost):
            raise BudgetExceeded(f"Cannot afford {cost}; remaining {self.remaining}")
        self.spent = self.spent + cost

    @property
    def remaining(self) -> Cost:
        return self.total - self.spent

    @property
    def exhausted(self) -> bool:
        return not self.can_afford(Cost())

    def view(self) -> BudgetView:
        return BudgetView(total=replace(self.total), spent=replace(self.spent))
