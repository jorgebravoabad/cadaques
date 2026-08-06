# ADR-0006: Failure is a scientific result; cost invariants have veto power

Status: accepted (2026-08)

## Context
A failed DFT run or dataset miss carries evidence and consumed money. In 0.1, an oracle exception aborts the loop and loses the ledger tail.
## Decision
Observations carry a status (REJECTED/SUBMITTED/RUNNING/COMPLETED/FAILED/CANCELLED/TIMED_OUT/PARTIALLY_COMPLETED) and optional FailureRecord; failures settle cost. Constitutionally: declared-vs-settled accounting and two-sided metering are invariants with veto power — a feature that lets any query or decision escape the ledger does not merge.
## Consequences
The runner converts exceptions into FAILED observations; retry/stop is campaign policy; the invariants appear in code review checklists.
