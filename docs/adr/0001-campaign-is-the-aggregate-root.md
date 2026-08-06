# ADR-0001: Campaign is the aggregate root

Status: accepted (2026-08)

## Context
The package could be organised around optimisers (as most libraries are) or around the campaign.
## Decision
The Campaign is the aggregate root: it owns proposal validation, submission, accounting, recording and stopping. Algorithms, oracles, resources and agents are replaceable participants behind protocols.
## Consequences
The public API converges on `Campaign(task=, driver=, oracle=|resource=, budget=)`; no participant may bypass the campaign's accounting.
