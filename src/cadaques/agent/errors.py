"""Structured errors: every failure says what the caller can do next.

A traceback is written for a person reading a terminal. An agent needs
a stable code to branch on, to know whether retrying the same call can
help, and to know which tool to call instead. :class:`ToolError`
carries exactly that, and :data:`ERROR_CODES` is the closed vocabulary
(ADR-0013): a new code is an interface change, documented in the
CHANGELOG like any other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

#: code -> (retryable by repeating the same call?, default next actions)
ERROR_CODES: dict[str, tuple[bool, tuple[str, ...]]] = {
    "invalid_arguments": (False, ("describe_capabilities",)),
    "invalid_actor": (False, ("describe_capabilities",)),
    "spec_invalid": (False, ("describe_capabilities", "validate_spec")),
    "unknown_campaign": (False, ("list_campaigns", "create_campaign")),
    "campaign_exists": (False, ("get_campaign_status",)),
    "campaign_stopped": (False, ("get_campaign_status", "get_events")),
    "budget_exhausted": (False, ("get_campaign_status", "stop_campaign")),
    "invalid_query": (False, ("get_campaign_status", "reserve_evaluation")),
    "unknown_evaluation": (False, ("get_campaign_status",)),
    "already_settled": (False, ("get_events",)),
    "pending_evaluations": (False, ("get_campaign_status", "settle_evaluation", "cancel_evaluation")),
    "unsupported_operation": (False, ("describe_capabilities", "get_campaign_status")),
    "idempotency_conflict": (False, ("get_events",)),
    "busy": (True, ()),
    "internal_error": (False, ("get_campaign_status",)),
}


@dataclass
class ToolError(Exception):
    """A failure a tool caller can act on."""

    code: str
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)
    next_actions: Sequence[str] | None = None
    retryable: bool | None = None

    def __post_init__(self) -> None:
        if self.code not in ERROR_CODES:
            raise ValueError(f"Unregistered error code {self.code!r}")
        default_retryable, default_next = ERROR_CODES[self.code]
        if self.retryable is None:
            self.retryable = default_retryable
        if self.next_actions is None:
            self.next_actions = default_next
        super().__init__(self.message)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": bool(self.retryable),
            "next_actions": list(self.next_actions or ()),
            "details": dict(self.details),
        }
