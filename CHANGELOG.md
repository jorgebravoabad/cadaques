# Changelog

All notable changes to CADAQUES. Semantic versioning; see ADR-0010 for
the pre-1.0 stability policy. The claim of record is the latest tag.
Concept DOI, resolving to the latest version:
[10.5281/zenodo.21293588](https://doi.org/10.5281/zenodo.21293588).

## [0.4.1] — 2026-10 — DOI: pending

A hardening release of the agent surface. The kernel validation of 0.4.0
(six properties over thousands of randomized campaigns, SIGKILL and fault-injection
runs) found four defects in the file-backed workspace; all are fixed here,
together with the O(1) tool-call work of ADR-0015 and its completion
(ADR-0016). One call changes behaviour: read **Changed** first.

<!-- TODO(author): when the release is tagged and archived, replace "DOI: pending"
     in the heading above with the version DOI Zenodo assigns, and add it to the
     release notes. CITATION.cff keeps the concept DOI, which does not change. -->

### Changed
- **BEHAVIOUR CHANGE: `settle_evaluation` requires `settled_cost`.** It used
  to charge the declared cost when `settled_cost` was omitted and record
  `settled_source: "declared_default"`, which let an unmeasured number enter
  the ledger as a measurement (ADR-0006). Omission now returns
  `invalid_arguments` ("settled_cost is required: report what the evaluation
  actually cost...") with `next_actions: ["settle_evaluation"]`, and commits
  nothing. Callers that relied on the default must pass `settled_cost`
  (`{}` is a legitimate report of zero cost). `cancel_evaluation` is
  unchanged: it still charges the declared cost by default. Logs recorded by
  0.4.0 may contain `declared_default`; they still load and replay.
- **Tool-call latency no longer grows with the log** (ADR-0015). Mutating
  calls commit by appending new events instead of rewriting the log, tool
  calls reuse a validated, incrementally folded session, and a
  `snapshot.json` (a cache, validated against the log and ignored on any
  mismatch) serves cold status and listing calls. `EventLog.from_jsonl`
  now ignores an unterminated, unparsable final line (a torn append).
  New internals: `StateFold`, `read_events_from`, `replay.restore`.
- **A new process no longer replays the log on its first call** (ADR-0016),
  for campaigns whose driver keeps no history (`ExternalDriver`). The
  snapshot is persisted as schema 2 (adds a checksum of the fold) with a
  request index, `events.index.json`; a new process starts from them after
  validating both against the log, folds forward the events appended since,
  and replays on any mismatch. Workspaces written by 0.4.0 or the ADR-0015
  branch are read as they are and upgraded by the next commit.
  `EventLog` gains `base` (events held out of memory).
- A call that appends several events is committed all or nothing: its first
  event carries `payload.call_events`, and a log that ends inside such a call
  (a power loss cut the append short) is read as ending before it (ADR-0016).
- The request journal is no longer scanned for a `request_id` the record
  has never seen; `Session` and the store are internal.

### Fixed
- **`spec.json` is written atomically** (tmp file, fsync, `os.replace`),
  like `checkpoint.json` and the event log. A SIGKILL during the write used
  to leave a 0-byte `spec.json` and an unreadable campaign.
  `CampaignSpec.dump` and `checkpoint()` both go through the new
  `core.fsio.write_atomic`.
- **Read-only tools no longer fail under concurrent writers.** A read that
  overlapped a writer could see a torn file and return `internal_error` (or
  `readable: false` in `list_campaigns`) for a healthy campaign. Reads are
  retried with a short back-off and then made once under the campaign lock
  in shared mode; a really damaged campaign still reports its error.
- **A retried call gets its original answer** (ADR-0017). A refused
  `reserve_evaluation` (`budget_exhausted`, `invalid_query`, driver
  stopped) that committed events and crashed before its journal entry was
  reconstructed as `ok: true` without an `evaluation_id`. The refusal is
  now recorded with the events (`payload.refusal`) and returned on retry;
  settle, cancel, run, stop and create retries return their original result
  keys too. Older records get the refusal inferred from their events.
- **A torn request journal no longer breaks every later call.** A crash
  mid-append left a fragment in `requests.jsonl` that the next write glued
  into a garbage line, and lookups then raised `JSONDecodeError`. The index
  skips lines that are not complete entries, a damaged line is not an
  answer, and the next append repairs the tail first (a fragment that only
  lacks its newline is kept; a torn one is cut off).
- `settle_evaluation` and `cancel_evaluation` now feed the result to the
  live campaign's history and driver, as a replay does. Before, a session
  kept by ADR-0015 never told a learning driver (`AnnealedLocalDriver`,
  `BayesianDriver`) about settled results, so it proposed different points
  from a session rebuilt from disk.
- The `stopped` event written when a reservation exhausts the budget records
  the campaign's true `n_queries`.

### Compatibility
- Event schema `cadaques.events/2` is unchanged; `payload.refusal` is an
  optional extra key on `driver_proposal`, `rejected` and `stopped` events.
  Tool input schemas are unchanged except that `settled_cost` is now required
  by `settle_evaluation`. No public Python interface removed.
- Workspaces from 0.4.0 are readable; 0.4.1 workspaces (their extra files
  `snapshot.json` schema 2 and `events.index.json` are caches) are readable
  by 0.4.0, which ignores them.
- 265 tests (155 in 0.4.0). The 0.4.0 kernel-validation suite (six properties,
  SIGKILL, fault injection and a reader-race reproduction) passes with no
  violation against this release.

## [0.4.0] — 2026-10 — DOI: [10.5281/zenodo.23474981](https://doi.org/10.5281/zenodo.23474981)

The agent-first surface: the kernel operated through typed tools by
agents and people alike, with the asynchronous, agent-operated
evaluation path the 0.1 paper listed as future work (ADR-0013, ADR-0014).

### Added
- **Agent surface** (`cadaques.agent`): eleven tools with JSON-Schema
  inputs and outputs and side-effect annotations
  (`describe_capabilities`, `validate_spec`, `create_campaign`,
  `list_campaigns`, `get_campaign_status`, `get_events`,
  `reserve_evaluation`, `settle_evaluation`, `cancel_evaluation`,
  `run_campaign`, `stop_campaign`); one response envelope; structured
  errors from a closed vocabulary with `retryable` and `next_actions`;
  file-backed workspace with per-campaign locks, so campaigns outlive
  sessions and processes; `request_id` idempotency with crash-window
  recovery; allowlist of spec-declarable participants.
- **Front ends**: `cadaques-agent call|schemas` JSON command line and
  `cadaques-agent serve-mcp` (extra `cadaques[mcp]`, MCP SDK 2.x).
- **Reservations** (ADR-0014): `evaluation_reserved` /
  `evaluation_cancelled` events; admission against declared cost plus
  outstanding reservations; settlement and cancellation always charged;
  `CampaignState.reserved`, `.pending`, `.n_cancelled`.
- **ExternalOracle** (`oracles/external.py`): declared-cost model for
  evaluations run outside the process (tariff with an optional power law
  in fidelity knobs). **ExternalDriver** (`drivers/external.py`): the
  operator proposes every point and reports its decision cost.
- `Campaign.run(actor=..., request_id=...)` attributes library runs too;
  new stop reason `operator_stopped`; `TERMINAL_STOP_REASONS`.
- `recommend()` accepts a finite candidate `pool=` (array in sorted
  parameter order, or parameter dicts) and an `exclude_measured=True`
  policy: successfully measured points are filtered out *before* ranking
  (constraint-filtering precedent), failed points remain recommendable,
  and replicates are available via `exclude_measured=False`; pool policy
  is recorded in the recommendation provenance. `BayesianDriver.rank()`
  validates the supplied pool's shape and documents that pool policy
  belongs to `recommend()`. Originates from an external review patch;
  the exclusion was relocated from the driver to the orchestrator.
- **Studies** (`studies/agent_operation/`): *draft* protocol v2 for the
  agent-operation experiment (not frozen), with the scheduler shim, a
  local `fakeslurm`, the grader and the quarter-wave reference check.

### Changed
- Events are written as `cadaques.events/2`, with `actor` and
  `request_id` on every event; schema-1 logs still load.
- `EventLog.to_jsonl` and `checkpoint()` write atomically; the event
  log is written last and is the commit point.
- Resumed failures keep their `retryable` flag.

### Fixed
- `ARCHITECTURE.md` no longer describes `Resource` as forthcoming
  (shipped in 0.2). (External review.)

### Compatibility
- No public interface removed; all 0.3, 0.2 and 0.1 imports and
  constructor calls unchanged; 155 tests.
- Event logs written by 0.4 use schema `cadaques.events/2` and cannot
  be read by 0.3 or earlier; 0.4 reads both schemas.
- The paper reproduction stays pinned at `v0.1.0-paper`.

## [0.3.0] — 2026-08 — DOI: [10.5281/zenodo.22028069](https://doi.org/10.5281/zenodo.22028069) 

The generality proof: retrospective campaigns over real data, cost-aware
intelligence, audited statistics, recommendation mode, and the first
domain vertical — on the unchanged 0.2 kernel.

### Added
- **DatasetOracle** (`oracles/dataset.py`): a measured table as an Oracle —
  declared miss policy (`nearest` within normalized tolerance, or FAILED
  `dataset_miss` settling the tariff: a miss consumes budget), deterministic
  economics with optional per-row recorded cost columns, `from_csv`,
  data-derived `.bounds`, and `.as_history()` for recommendation mode.
- **BayesianDriver** (`drivers/bo.py`, extra `cadaques[bo]`): Matérn GP +
  Expected Improvement per predicted unit cost (cost surrogate fitted on the
  driver's own settled history), budget-annealed exploration via
  `fraction_used`, cost-aware flat-EI fallback, `rank()` for batch scoring;
  scikit-learn stays out of core (lazy import).
- **cadaques.stats**: paired bootstrap CI and Wilcoxon signed-rank on NumPy
  alone (exact null to n=25, tie/continuity-corrected approximation beyond),
  `compare()`/`Comparison.summary()`, and seed-paired `paired_campaigns()`.
- **Recommendation mode** (`runtime/recommend.py`): `recommend()` →
  `RankedCandidates` — seeded bit-for-bit reproducible, provenanced (driver
  spec, history sha256, schema `cadaques.recommendation/1`), metered thinking
  time, `to_csv`/`to_json`; task constraints filter the candidate pool
  *before* ranking.
- **Chemistry vertical** (`verticals/chemistry.py`): `ReactionTable` — column
  roles (composition, conditions, objective, cost) compiled to kernel
  objects; `recommend_next()` and `campaign()` frontends; end-to-end
  `examples/chemistry_recommend.py`.
- **Studies package** (`studies/`): frozen protocol v1 for the vertical paper
  — pinned quartet (Ising, OCM-291 featurized, Olympus snar/fullerenes with
  real per-row costs), 20 paired seeds × 4 drivers, pre-registered honesty
  notes, `run_study.py --smoke` verified on all four.

### Changed
- Reference drivers consume rng in **sorted parameter order** (ADR-0012), so
  canonical-JSON spec round-trips and dict orderings cannot alter a seeded
  campaign. Seeded sequences differ from 0.2 in multi-parameter spaces; the
  paper reproduction stays pinned at `v0.1.0-paper`.
- CI runs on all branches and installs `[dev,bo]`.

### Compatibility
- No interface changes; all 0.2 and 0.1 imports and constructor calls
  unchanged; 107 tests.

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

## [0.1.0] — 2026 — DOI: [10.5281/zenodo.21293589](https://doi.org/10.5281/zenodo.21293589)

Initial public release: the cost-aware Driver–Oracle architecture.
Metered `Oracle` and `Driver` protocols; multi-currency `Cost`,
`Budget`, `BudgetView`; declared vs. settled accounting with the
discrepancy as an observable; both sides metered (the price of
intelligence); `Campaign` runner and append-only JSONL `Ledger`;
`AnnealedLocalDriver` and `RandomDriver`; `Ising2DOracle` with
fidelity-dependent cost and exact ground truth; `AnalyticOracle`.
Tagged `v0.1.0`; archived on Zenodo; PyPI `cadaques`.
