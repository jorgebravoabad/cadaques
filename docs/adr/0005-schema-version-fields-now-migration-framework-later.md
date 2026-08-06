# ADR-0005: Schema version fields now; migration framework later

Status: accepted (2026-08)

## Context
Serialized events, checkpoints and specs will evolve.
## Decision
Every serialized record carries a schema-version field from its first release. A migration framework is deferred until a real schema v2 and external users exist (decision gate).
## Consequences
Cheap insurance now; no speculative machinery.
