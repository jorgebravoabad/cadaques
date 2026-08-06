# ADR-0009: Plugins by ordinary packaging and entry points

Status: accepted (2026-08)

## Context
External Drivers, Oracles, Resources and Executors should not require touching this repository.
## Decision
Standard Python entry points (groups cadaques.drivers / cadaques.oracles / cadaques.resources); separate repositories only on genuinely independent lifecycle, dependencies, maintainers or users.
## Consequences
No bespoke plugin framework; the expected family (cadaques-botorch, cadaques-slurm, ...) stays out of core.
