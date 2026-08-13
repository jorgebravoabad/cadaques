"""CADAQUES — Cost-Aware Dual Architecture for QUery-Efficient diScovery.

An open-source framework for autonomous discovery campaigns:
any oracle, any driver, one budget. Every query counts.
"""

from .runtime.campaign import Campaign, CampaignResult, DriverStopped, Outcome
from . import stats
from .runtime.replay import checkpoint, replay, resume
from .runtime.state import CampaignState, reduce
from .core.cost import Budget, BudgetExceeded, BudgetView, Cost
from .core.ledger import Ledger, Transaction
from .core.artifacts import ArtifactRef, store_artifact
from .core.events import Event, EventLog
from .core.observation import Action, FailureRecord, Observation, ObservationStatus
from .core.records import Query, Result
from .core.spec import CampaignSpec, SpecError
from .core.task import InvalidQuery, SearchSpace, Task
from .protocols.driver import Driver
from .protocols.oracle import Oracle
from .protocols.resource import JobHandle, JobStatus, OracleResource, Resource

__version__ = "0.3.0.dev0"

__all__ = [
    "Budget", "BudgetExceeded", "BudgetView", "Campaign", "CampaignResult", "CampaignSpec", "SpecError",
    "Cost", "Driver", "Event", "EventLog", "DriverStopped", "Ledger", "Oracle", "OracleResource", "Query", "Resource",
    "Action", "FailureRecord", "Observation", "ObservationStatus", "Outcome", "CampaignState", "Result", "SearchSpace", "store_artifact", "checkpoint", "replay", "resume", "Task", "InvalidQuery", "Transaction", "__version__",
]
