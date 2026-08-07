# CADAQUES architecture

*The campaign is the aggregate root. Everything else is a replaceable participant.*

CADAQUES organises autonomous discovery around one invariant loop: a **Driver**
proposes, an **Oracle** (or, soon, a **Resource**) evaluates at a price, the
**Campaign** validates, accounts and records, and the loop ends when the
**Budget** is exhausted — not when an iteration counter runs out.

Two constitutional invariants (ADR-0006, principles table of the three-year
strategy) have veto power over every feature:

1. **Declared vs. settled cost.** Every query is priced *ex ante* and settled
   *ex post*; the discrepancy is a recorded observable.
2. **Metered intelligence.** Both sides of the loop are metered: driver
   decisions cost wall time — and tokens, when the driver is an agent.

## Layers and dependency rules

| Layer | Modules | May depend on |
|---|---|---|
| `core/` | `cost`, `ledger`, `records` `task`, `observation`, `events`, `artifacts`, `spec` | stdlib, minimal NumPy |
| `protocols/` | `driver`, `oracle`, `resource` (roadmap: `executor`) | `core` only |
| `runtime/` | `campaign`, `state`, `replay` (roadmap: `executor` integration) | `core`, `protocols` |
| `drivers/`, `oracles/` | reference implementations | `core`, `protocols` |
| plugins (external) | BO libraries, schedulers, agents, instruments | public API |

Arrows point inward only. Drivers never import oracles; nothing imports plugins.

## The twelve kernel objects

| Object | Status | Home |
|---|---|---|
| Task | **shipped** (0.2) | `core/task.py` |
| Action / Observation | **shipped** (0.2; `Query`/`Result` remain their evaluation specializations, ADR-0002) | `core/observation.py`, `core/records.py` |
| Driver | **shipped** | `protocols/driver.py` |
| Oracle | **shipped** | `protocols/oracle.py` |
| Resource | **shipped** (0.2, ADR-0003 dual protocol; `OracleResource` adapter) | `protocols/resource.py` |
| Campaign | **shipped** | `runtime/campaign.py` |
| Budget | **shipped** | `core/cost.py` |
| Event | **shipped** (0.2; the Ledger is now a derived view) | `core/events.py` |
| Artifact | **shipped** (0.2) | `core/artifacts.py` |
| Outcome | **shipped** (0.2 rename; `CampaignResult` alias permanent) | `runtime/campaign.py` |
| CampaignSpec | **shipped** (0.2) | `core/spec.py` |

The status column is the claim of record: anything marked roadmap is a plan,
not a capability, and public descriptions must not promote it early.

## Decision log

Durable decisions live in `docs/adr/`. No new core abstraction is admitted
without a realistic use case and one rejected alternative (ADR review rule).
