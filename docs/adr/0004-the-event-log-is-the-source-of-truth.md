# ADR-0004: The event log is the source of truth

Status: accepted (2026-08)

## Context
The 0.1 ledger already records every transaction (declared, settled, meta) append-only.
## Decision
The ledger evolves into an event log covering campaign lifecycle (started, proposal, rejected, observation, failure, budget, checkpoint, stopped). State, checkpoints, reports and the Outcome are derived from events through a reducer. The Ledger's public accounting API is preserved as a derived view.
## Consequences
Replay, crash recovery and audit come from one mechanism; no component may hold state the events cannot reconstruct.
