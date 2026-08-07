"""Conformance checks for CADAQUES participants.

Contract testing outranks line coverage (three-year strategy, §8):
any Oracle, Driver or Resource — shipped here or written by anyone
else — should pass these checks. External adapter packages are
encouraged to import and run them in their own test suites.
"""

from .contracts import (
    check_driver_contract,
    check_oracle_contract,
    check_resource_contract,
)

__all__ = [
    "check_driver_contract",
    "check_oracle_contract",
    "check_resource_contract",
]
