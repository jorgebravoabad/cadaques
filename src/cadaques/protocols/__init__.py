"""Contracts of the dual architecture: Driver, Oracle (and, next, Resource, Executor)."""

from .driver import Driver
from .oracle import Oracle

__all__ = ["Driver", "Oracle"]
