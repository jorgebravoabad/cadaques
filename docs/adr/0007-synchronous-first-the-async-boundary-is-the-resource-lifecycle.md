# ADR-0007: Synchronous first; the async boundary is the Resource lifecycle

Status: accepted (2026-08)

## Context
Async execution (queues, out-of-order completion, retries) is required for HPC but is the classic over-engineering trap.
## Decision
The synchronous loop stays the reference semantics with bit-identical replay. Asynchrony enters only through Resource submit/status/collect plus an Executor, with ordered-complete event logging as its guarantee. No async work before stable sync contracts and replay (decision gate).
## Consequences
0.2 ships sync-only; PooledExecutor and Slurm come later on frozen semantics.
