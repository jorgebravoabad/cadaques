"""CADAQUES — Cost-Aware Dual Architecture for QUery-Efficient diScovery.

An open-source framework for autonomous discovery campaigns:
any oracle, any driver, one budget. Every query counts.
"""

from .core.campaign import Campaign, CampaignResult, DriverStopped
from .core.cost import Budget, BudgetExceeded, BudgetView, Cost
from .core.ledger import Ledger, Transaction
from .core.protocols import Driver, Oracle, Query, Result

__version__ = "0.1.0.dev0"

__all__ = [
    "Budget", "BudgetExceeded", "BudgetView", "Campaign", "CampaignResult",
    "Cost", "Driver", "DriverStopped", "Ledger", "Oracle", "Query",
    "Result", "Transaction", "__version__",
]
