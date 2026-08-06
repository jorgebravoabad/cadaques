# ADR-0012: Determinism discipline: one seed, named streams

Status: accepted (2026-08)

## Context
Replay requires controlled randomness; 0.1 seeds live per-driver.
## Decision
All randomness flows from the campaign seed through named numpy Generator streams (driver, sampler, executor) injected into participants that accept rng=. Per-participant seeds remain supported through a deprecation cycle. Replay equivalence is the CI-enforced consequence.
## Consequences
Two runs with the same spec produce identical ledgers; any PR touching the loop must pass the replay job.
