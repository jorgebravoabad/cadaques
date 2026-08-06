"""Core data objects: costs, budgets, ledger, and the typed records.

For 0.1 compatibility this package also re-exports the protocol and
runtime symbols that lived here before the 0.2 layout split; those
are resolved lazily to avoid import cycles (canonical homes:
``cadaques.protocols`` and ``cadaques.runtime``).
"""

from .artifacts import ArtifactRef, store_artifact
from .cost import Budget, BudgetExceeded, BudgetView, Cost
from .ledger import Ledger, Transaction
from .observation import Action, FailureRecord, Observation, ObservationStatus
from .records import Query, Result
from .task import Bounds, Constraint, Direction, InvalidQuery, SearchSpace, Task

_MOVED = {
    "Campaign": "cadaques.runtime.campaign",
    "CampaignResult": "cadaques.runtime.campaign",
    "DriverStopped": "cadaques.runtime.campaign",
    "Driver": "cadaques.protocols.driver",
    "Oracle": "cadaques.protocols.oracle",
}

__all__ = [
    "Budget", "BudgetExceeded", "BudgetView", "Campaign", "CampaignResult",
    "Cost", "Driver", "DriverStopped", "Ledger", "Oracle", "Query",
    "Result", "Transaction",
]


def __getattr__(name: str):
    if name in _MOVED:
        import importlib

        return getattr(importlib.import_module(_MOVED[name]), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
