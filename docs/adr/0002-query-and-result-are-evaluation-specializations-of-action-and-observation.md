# ADR-0002: Query and Result are evaluation specializations of Action and Observation

Status: accepted (2026-08)

## Context
The strategy's kernel names Action/Observation; the shipped, cited 0.1 API names Query/Result. Renaming a published protocol for vocabulary symmetry is churn.
## Decision
Query and Result stay, defined as the evaluation-specialized envelopes. The general Action (operation, parameters, fidelity, metadata) and Observation (values, uncertainty, status, cost, artifacts, diagnostics) arrive with the Resource protocol; Query/Result subclass or map onto them. No deprecation of Query/Result is planned.
## Consequences
0.1 code runs forever; the general envelopes must be designed so the specialization is exact (Result.value backed by Observation.values).
