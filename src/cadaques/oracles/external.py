"""The :class:`ExternalOracle`: work the kernel prices but does not run.

In an agent-operated campaign (ADR-0014) the evaluation happens outside
the Python process — a batch job on a cluster, a measurement on an
instrument — and comes back later through ``settle_evaluation``. The
kernel still owns the *declared* cost: admission must not depend on a
number the operator invents per request. This oracle is that price
model, and nothing else.

The model is a declared tariff with an optional power law in the
fidelity knobs, enough to express e.g. a band-structure solver whose
cost grows as ``resolution**d``::

    ExternalOracle(tariff={"seconds": 30.0},
                   scaling={"resolution": 2.0},
                   reference={"resolution": 16})

prices a ``resolution=32`` query at 120 s. Fields are JSON-able, so the
oracle rides a :class:`~cadaques.core.spec.CampaignSpec` unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from ..core.cost import Cost
from ..core.records import Query, Result


class ExternalEvaluation(RuntimeError):
    """An ExternalOracle was asked to evaluate in-process."""


@dataclass
class ExternalOracle:
    """Declared-cost model for evaluations performed outside the kernel.

    Parameters
    ----------
    tariff:
        Declared cost of one query at the reference fidelity, as a
        ``{currency: amount}`` mapping.
    scaling:
        Optional ``{fidelity_knob: exponent}``. The tariff is multiplied
        by ``(fidelity[knob] / reference[knob]) ** exponent`` per knob.
    reference:
        Reference value of each scaled knob; a knob missing from a
        query's fidelity is taken at its reference value.
    """

    tariff: Mapping[str, float] = field(default_factory=lambda: {"seconds": 1.0})
    scaling: Mapping[str, float] = field(default_factory=dict)
    reference: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        Cost.from_dict(dict(self.tariff))  # unknown currencies fail at construction
        missing = set(self.scaling) - set(self.reference)
        if missing:
            raise ValueError(f"Scaled knobs need a reference value: {sorted(missing)}")
        bad = {k: v for k, v in self.reference.items() if not float(v) > 0}
        if bad:
            raise ValueError(f"Reference fidelities must be positive: {bad}")

    def price(self, query: Query) -> Cost:
        factor = 1.0
        for knob, exponent in self.scaling.items():
            value = float(query.fidelity.get(knob, self.reference[knob]))
            factor *= (value / float(self.reference[knob])) ** float(exponent)
        return Cost.from_dict(dict(self.tariff)).scaled(factor)

    def evaluate(self, query: Query) -> Result:
        raise ExternalEvaluation(
            "ExternalOracle evaluations run outside the kernel: reserve them with "
            "reserve_evaluation and report them with settle_evaluation."
        )
