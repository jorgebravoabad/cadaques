# ADR-0010: Semantic versioning and pre-1.0 guarantees

Status: accepted (2026-08)

## Context
Early adopters and the published paper need to know what is safe.
## Decision
Semver; nothing public breaks without a deprecation shim spanning at least two minor releases; the exact paper implementation is permanently tagged (v0.1.0, v0.1.0-paper) and its examples stay runnable in CI; CHANGELOG with migration notes for every breaking release.
## Consequences
cadaques.compat-style shims are permanent citizens; CI runs the paper example against every release.
