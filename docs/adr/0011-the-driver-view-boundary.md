# ADR-0011: The driver view boundary

Status: accepted (2026-08)

## Context
0.1 already hands drivers a read-only BudgetView rather than the Budget or Ledger.
## Decision
Constitutional: Drivers receive only read-only projections (history, BudgetView; later a CampaignView) — never the Ledger, the raw event log, or other participants. Neutrality among methods and guest safety rest on this line.
## Consequences
Guest adapters (external BO, agents) cannot corrupt accounting; explanation features read the same projections.
