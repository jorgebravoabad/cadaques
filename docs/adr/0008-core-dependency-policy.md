# ADR-0008: Core dependency policy

Status: accepted (2026-08)

## Context
Research packages die of dependency weight.
## Decision
core/ uses the standard library plus minimal NumPy; protocols/ admits no domain frameworks; runtime/ depends only on core and protocols; heavy methods (GP/BO stacks, schedulers, agents) live behind optional extras or external plugin packages.
## Consequences
`pip install cadaques` stays light; extras like [bo] and [agents] carry the weight.
