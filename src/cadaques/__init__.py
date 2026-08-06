"""CADAQUES — Cost-Aware Dual Architecture for QUery-Efficient diScovery.

An open-source framework for autonomous discovery campaigns:
any oracle, any driver, one budget. Every query counts.
"""

from .runtime.campaign import Campaign, CampaignResult, DriverStopped
from .core.cost import Budget, BudgetExceeded, BudgetView, Cost
from .core.ledger import Ledger, Transaction
from .core.observation import Action, FailureRecord, Observation, ObservationStatus
from .core.records import Query, Result
from .core.task import InvalidQuery, SearchSpace, Task
from .protocols.driver import Driver
from .protocols.oracle import Oracle

__version__ = "0.2.0.dev0"

__all__ = [
    "Budget", "BudgetExceeded", "BudgetView", "Campaign", "CampaignResult",
    "Cost", "Driver", "DriverStopped", "Ledger", "Oracle", "Query",
    "Action", "FailureRecord", "Observation", "ObservationStatus", "Result", "SearchSpace", "Task", "InvalidQuery", "Transaction", "__version__",
]
