# Changelog

All notable changes to CADAQUES. Semantic versioning; see ADR-0010 for
the pre-1.0 stability policy. The claim of record is the latest tag.

## [Unreleased]
## [0.3.0] — 2026-08
## [0.2.0] — 2026-08 — DOI: [10.5281/zenodo.21915298](https://doi.org/10.5281/zenodo.21915298)

The campaign becomes a durable object: the twelve-object kernel of the
campaign architecture, event-sourced and replayable, with full 0.1
compatibility.

### Added
- **Layout**: `cadaques.protocols` (contracts) and `cadaques.runtime`
  (loop) split out of `core`; `ARCHITECTURE.md` and ADRs 0001–0012.
- **Task** (`core/task.py`): `SearchSpace` with validation and sampling,
  `Task` with direction, pure declarative constraints, fidelity defaults
  and an optional success criterion; `Campaign(task=...)`.
- **Envelopes** (`core/observation.py`): general `Action`/`Observation`
  with exact lossless conversions to the shipped `Query`/`Result`
  (ADR-0002); eight-status vocabulary; `FailureRecord`;
  `Result.failed()` — failures settle cost.
- **Artifact** (`core/artifacts.py`): `ArtifactRef` and a local
  content-addressed store.
- **Event log** (`core/events.py`): schema-versioned JSONL
  (`cadaques.events/1`), sequence-numbered, round-tripping; the
  accounting `Ledger` is now a derived view (`Ledger.from_events`).
- **CampaignSpec** (`core/spec.py`): the declarative campaign definition
  (`cadaques.spec/1`); `to_spec()/from_spec()` with exact round-trip;
  participants holding callables are refused with `SpecError`.
- **Resource** (`protocols/resource.py`): submit/status/collect/cancel
  lifecycle with `estimate()`; `OracleResource` adapts any Oracle
  (ADR-0003 dual protocol).
- **Derived state** (`runtime/state.py`): `CampaignState` as a pure fold
  of events, including declared/settled totals and `.overrun`;
  `Outcome.state` with live/derived equivalence tested.
- **Seed streams** (ADR-0012): `Campaign(seed=...)` spawns named streams
  into participants implementing `reseed(rng)`; the seed rides spec and
  events.
- **Replay / checkpoint / resume** (`runtime/replay.py`):
  `replay(spec, events)` derives final state with no re-execution;
  kill-and-resume is query-for-query identical to an uninterrupted run
  (deterministic projection; settled wall time is physical).
- **Conformance suites** (`cadaques.testing`): public Oracle/Driver/
  Resource contract checks; all shipped participants pass.

### Changed
- `Campaign.run()` converts oracle exceptions into FAILED results that
  keep their declared cost on the record (previously the loop aborted
  and lost the ledger tail); out-of-task proposals become recorded
  `rejected` events bounded by `max_consecutive_rejections`; new stop
  reasons `rejection_limit` and `success`.
- `CampaignResult` is renamed `Outcome`; the old name remains a
  permanent alias (ADR-0010).
- `AnnealedLocalDriver.observe` ignores failed results (a NaN can no
  longer poison its incumbent).

### Compatibility
- All 0.1 imports keep working (`cadaques.core.protocols`,
  `cadaques.core.campaign`); every 0.1 constructor call is unchanged;
  the 0.1 test suite passes unmodified.

## [0.1.0] — 2026

Initial public release: the cost-aware Driver–Oracle architecture.
Metered `Oracle` and `Driver` protocols; multi-currency `Cost`,
`Budget`, `BudgetView`; declared vs. settled accounting with the
discrepancy as an observable; both sides metered (the price of
intelligence); `Campaign` runner and append-only JSONL `Ledger`;
`AnnealedLocalDriver` and `RandomDriver`; `Ising2DOracle` with
fidelity-dependent cost and exact ground truth; `AnalyticOracle`.
Tagged `v0.1.0`; archived on Zenodo; PyPI `cadaques`.
