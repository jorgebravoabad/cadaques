# ADR-0003: Oracle versus Resource: a dual protocol, not a rename

Status: accepted (2026-08)

## Context
Synchronous evaluation (functions, datasets, fast simulators) and operational science (Slurm, DFT, instruments) have different lifecycles. Earlier strategy drafts considered renaming Oracle to Resource, or keeping Oracle alone.
## Decision
Both, as different contracts: Oracle = price/evaluate (synchronous); Resource = submit/status/collect (job lifecycle). An OracleResource adapter wraps any Oracle as a Resource. This supersedes both earlier positions; the acronym's dual architecture survives intact.
## Consequences
The runtime targets Resource internally once it exists; Oracle never breaks; documentation teaches Oracle first.
