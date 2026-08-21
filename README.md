# CADAQUES

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.21293589.svg)](https://doi.org/10.5281/zenodo.21293589)
[![PyPI](https://img.shields.io/pypi/v/cadaques.svg)](https://pypi.org/project/cadaques/)
[![CI](https://github.com/jorgebravoabad/cadaques/actions/workflows/ci.yml/badge.svg)](https://github.com/jorgebravoabad/cadaques/actions/workflows/ci.yml)

**Cost-Aware Dual Architecture for QUery-Efficient diScovery**

*An open-source framework for autonomous discovery campaigns: any oracle, any driver, one budget.*

CADAQUES decouples autonomous discovery into two symmetric protocols. A metered
**Oracle** abstracts anything that answers queries at a price — a simulator, a
laboratory instrument, an analytic function. A **Driver** abstracts anything that
decides what to ask next — random search, Bayesian optimization, gradient methods,
LLM agents. Between them sits the framework's one structural commitment: **cost is
a first-class primitive**. Every oracle query and every driver decision is priced
in heterogeneous currencies (wall time, CPU hours, euros, tokens) and charged
against a single campaign budget. Campaigns end when the budget is exhausted, not
when an iteration counter runs out, and all results are reported as performance
per unit cost. *Every query counts.*

## Installation

```bash
pip install cadaques          # core: NumPy only
pip install "cadaques[bo]"    # + the Gaussian-process driver (scikit-learn)
```

Or, for development, install the latest version from source:

```bash
git clone https://github.com/jorgebravoabad/cadaques.git
cd cadaques
pip install -e .
```

## Quickstart: a discovery campaign with an exact answer

Locate the critical temperature of the 2D Ising model by maximizing the magnetic
susceptibility under a fixed compute budget. Onsager's exact result,
T_c = 2/ln(1+√2) ≈ 2.269, provides the ground truth against which any driver
can be validated.

```python
from cadaques import Budget, Campaign, Cost
from cadaques.drivers import AnnealedLocalDriver
from cadaques.oracles import Ising2DOracle, T_C_EXACT

oracle = Ising2DOracle(seed=0)
driver = AnnealedLocalDriver(
    space={"T": (1.5, 3.5)},
    fidelity={"L": 24, "sweeps": 400},
    seed=0,
)
campaign = Campaign(oracle, driver, Budget(total=Cost(seconds=30.0)))

outcome = campaign.run()
print(f"Best T = {outcome.best.query.params['T']:.3f}  (exact: {T_C_EXACT:.3f})")
print(f"Queries: {outcome.n_queries}, stop reason: {outcome.stop_reason}")
print(f"Spent: {outcome.budget.spent}")
```

## The campaign is a durable object (since 0.2)

Since 0.2 a campaign is not a script you ran once — it is a declarative,
inspectable, replayable object:

```python
from cadaques import Budget, Campaign, Cost, Task, checkpoint, replay, resume
from cadaques.drivers import RandomDriver
from cadaques.oracles import Ising2DOracle

task = Task.from_bounds({"T": (1.5, 3.5)}, name="ising_tc")
campaign = Campaign(
    Ising2DOracle(), RandomDriver(space=task.space.bounds),
    Budget(total=Cost(seconds=30.0)),
    task=task,      # direction, bounds, constraints, success criterion
    seed=42,        # one campaign seed rules all randomness (ADR-0012)
)

spec = campaign.to_spec()            # the campaign as portable JSON
outcome = campaign.run()
outcome.events.to_jsonl("run.jsonl") # append-only event log: the source of truth

# audit later, with no re-execution — derived state equals the live state:
assert replay(spec, "run.jsonl") == outcome.state

# or kill it and continue exactly where it stopped:
checkpoint(campaign, "ckpt/")
revived = resume("ckpt/")
```

What this buys, concretely:

- **Event-sourced runtime.** Every transition — start, proposal, rejection,
  result, failure, stop — is an immutable event (JSONL, schema-versioned).
  The accounting ledger is a *derived view* of the event log.
- **Failure is a result.** An oracle exception becomes a FAILED `Result`
  that settles its declared cost and stays on the record; out-of-task
  proposals are recorded rejections, never crashes.
- **Replay and resume.** Final state derives from `(spec, events)` alone;
  a checkpointed campaign continues query-for-query identically to an
  uninterrupted one (rng states included).
- **Specs refuse to lie.** Participants holding callables are not
  spec-serializable and are refused loudly — a spec is a portable
  declaration, never a pickled blob.
- **Conformance suites.** `cadaques.testing` ships the Oracle/Driver/
  Resource contract checks; external adapters are encouraged to run them.

## From a spreadsheet to the next experiments (0.3)

The first domain vertical. A chemist declares column roles once — composition
descriptors, reaction conditions, an objective, a recorded cost per run — and
the table compiles to kernel objects:

```python
from cadaques.verticals import ReactionTable

table = ReactionTable.from_csv(
    "reactions.csv",
    objective="c2_yield",
    composition=("x_Mn", "x_Ce"),
    conditions=("T", "P"),
    cost_columns={"seconds": "run_s"},
)

# Recommendation mode: which experiments should the lab run next?
ranking = table.recommend_next(k=5, seed=42)     # requires: pip install "cadaques[bo]"
print(ranking.summary())
ranking.to_csv("next_experiments.csv")           # for the lab notebook

# Campaign mode: would Bayesian optimization have beaten random on this table?
from cadaques import Budget, Cost
from cadaques.drivers import BayesianDriver, RandomDriver
from cadaques.stats import paired_campaigns

result = paired_campaigns(
    lambda seed: table.campaign(BayesianDriver(space=table.oracle().bounds),
                                Budget(total=Cost(seconds=25000)), seed=seed),
    lambda seed: table.campaign(RandomDriver(space=table.oracle().bounds),
                                Budget(total=Cost(seconds=25000)), seed=seed),
    seeds=range(20), metric=lambda o: o.best.value, alternative="greater",
)
print(result.summary())   # paired bootstrap CI + Wilcoxon, quotable as-is
```

Behind this sit the 0.3 components: **`DatasetOracle`** (a measured table as a
hidden oracle, with a *declared* miss policy — a dataset miss is a FAILED result
that settles its declared cost — and deterministic economics that can replay
recorded per-row costs); **`BayesianDriver`** (Matérn GP with Expected
Improvement *per predicted unit cost*, learned from the driver's own settled
history, exploration annealed with `fraction_used`); **`cadaques.stats`** (paired
bootstrap and Wilcoxon on NumPy alone, exact null to n=25); and **recommendation
mode** (`recommend()` → a ranked, seeded, bit-for-bit reproducible object whose
provenance records the driver's declaration, a fingerprint of the history, and
the measured *thinking time* — the price of intelligence, attached to the advice
it produced). A frozen retrospective-benchmark protocol over four public
datasets lives in `studies/`.

## Design principles

- **Dual agnosticism.** Oracles and Drivers are `typing.Protocol` classes with
  two methods each. Anything that speaks the protocol plugs in. Since 0.2 the
  dual architecture has an operational counterpart: `Resource`
  (submit/status/collect) with an `OracleResource` adapter.
- **Declared vs. settled cost.** Oracles declare a price *ex ante*
  (`oracle.price(query)`); the actual cost is settled *ex post* inside each
  `Result`. Real oracles deviate from their estimates — the ledger records both,
  and the discrepancy is itself an observable.
- **Both sides are metered.** Driver decisions cost wall time — and tokens, if the
  driver is an LLM agent. A campaign's economics include the price of intelligence,
  enabling the question: *when does an expensive smart driver beat a cheap dumb one?*
- **Failure is a result.** A failed evaluation — an oracle exception, a dataset
  miss — settles its declared cost and stays on the record, exactly like a wasted
  experiment.
- **Budget-aware strategies.** Drivers receive a read-only `BudgetView` and may
  adapt: the reference `AnnealedLocalDriver` explores while rich and exploits
  while poor; `BayesianDriver` anneals its exploration margin the same way.
- **The event log is the provenance.** Every transition (declared, settled,
  timestamped, schema-versioned) exports to JSONL: a complete, replayable trace
  of the campaign, from which the accounting ledger is derived.

## Status, stability and roadmap

**Status: research software, pre-1.0.** The claim of record is the latest
tagged release — this README describes shipped capability only, and the
roadmap below is a plan, not a feature list.

*Stability policy (ADR-0010).* Semantic versioning; nothing public breaks
without a deprecation shim spanning at least two minor releases. The exact
implementation accompanying v1 of the arXiv paper is permanently tagged
(`v0.1.0-paper`), and its imports run against every release via compatibility
shims (`cadaques.core.protocols`, `cadaques.core.campaign`, `CampaignResult`).
Durable design decisions live in `docs/adr/`.

*Kernel (shipped in 0.2).* Task · Query/Result and the general
Action/Observation envelopes · Driver · Oracle · Resource (with the
`OracleResource` adapter) · Campaign · Budget · Event · Artifact ·
Outcome · CampaignSpec — the twelve objects of the campaign
architecture, with event-sourced state, seed streams, replay,
checkpoint/resume and public conformance suites (`cadaques.testing`).

*Generality proof (shipped in 0.3).* `DatasetOracle` for retrospective
campaigns over measured tables; the cost-aware `BayesianDriver` (extra
`cadaques[bo]`); the audited `cadaques.stats` module; recommendation mode
(`recommend()`, `RankedCandidates`); the chemistry vertical
(`ReactionTable`); and the frozen study protocol in `studies/`.

*Roadmap (not yet shipped).* Asynchronous executors and a Slurm-backed
workflow resource behind the frozen Resource lifecycle (ADR-0007); a
portfolio Driver allocating budget across strategies as a cost-aware
bandit; native categorical and mixed search spaces; declarative
constraint vocabulary for specs; plugin entry points for external
drivers and oracles; an LLM-agent driver adapter with token metering.
See `ARCHITECTURE.md` and `docs/adr/` for boundaries and decision gates.

## Citation

If you use CADAQUES in academic work, please cite it (see `CITATION.cff`).
Concept DOI (always resolves to the latest version):
[10.5281/zenodo.21293589](https://doi.org/10.5281/zenodo.21293589).
Version DOIs are listed per release in [`CHANGELOG.md`](CHANGELOG.md).
Paper: [arXiv:2607.16127](https://arxiv.org/abs/2607.16127).

## License

MIT.
