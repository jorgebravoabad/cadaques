"""Contracts of the dual architecture: Driver, Oracle (and, next, Resource, Executor)."""

from .driver import Driver
from .oracle import Oracle
from .resource import JobHandle, JobStatus, OracleResource, Resource

__all__ = ["Driver", "JobHandle", "JobStatus", "Oracle", "OracleResource", "Resource"]
