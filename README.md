# CADAQUES

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.21293589.svg)](https://doi.org/10.5281/zenodo.21293589)
[![PyPI](https://img.shields.io/pypi/v/cadaques.svg)](https://pypi.org/project/cadaques/)

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
pip install cadaques
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

## The campaign is a durable object (0.2)

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

## Design principles

- **Dual agnosticism.** Oracles and Drivers are `typing.Protocol` classes with
  two methods each. Anything that speaks the protocol plugs in.
- **Declared vs. settled cost.** Oracles declare a price *ex ante*
  (`oracle.price(query)`); the actual cost is settled *ex post* inside each
  `Result`. Real oracles deviate from their estimates — the ledger records both,
  and the discrepancy is itself an observable.
- **Both sides are metered.** Driver decisions cost wall time — and tokens, if the
  driver is an LLM agent. A campaign's economics include the price of intelligence,
  enabling the question: *when does an expensive smart driver beat a cheap dumb one?*
- **Budget-aware strategies.** Drivers receive a read-only `BudgetView` and may
  adapt: the reference `AnnealedLocalDriver` explores while rich and exploits
  while poor.
- **The ledger is the provenance.** Every transaction (declared, settled,
  timestamped) exports to JSONL: a complete, replayable trace of the campaign.

## Status and roadmap

`0.1.0` — core protocols, campaign runner, multi-currency budget and ledger,
reference drivers, and a canonical Ising-2D oracle with fidelity-dependent cost.
Available on [PyPI](https://pypi.org/project/cadaques/) and archived on
[Zenodo](https://doi.org/10.5281/zenodo.21293589).

This is an early release: the API may evolve until `1.0`. Planned next steps
include a Bayesian-optimization driver, campaign replay, expanded documentation,
and an LLM-agent driver adapter.

## Citation

If you use CADAQUES in academic work, please cite it (see `CITATION.cff`).
DOI: [10.5281/zenodo.21293589](https://doi.org/10.5281/zenodo.21293589)

## License

MIT.

## Status, stability and roadmap

**Status: research software, pre-1.0.** The claim of record is the latest
tagged release — this README describes shipped capability only, and the
roadmap below is a plan, not a feature list.

*Stability policy (ADR-0010).* Semantic versioning; nothing public breaks
without a deprecation shim spanning at least two minor releases. The exact
implementation accompanying the arXiv paper is permanently tagged
(`v0.1.0`) and its imports run against every release via compatibility
shims (`cadaques.core.protocols`, `cadaques.core.campaign`,
`CampaignResult`). Durable design decisions live in `docs/adr/`.

*Kernel (shipped in 0.2).* Task · Query/Result and the general
Action/Observation envelopes · Driver · Oracle · Resource (with the
`OracleResource` adapter) · Campaign · Budget · Event · Artifact ·
Outcome · CampaignSpec — the twelve objects of the campaign
architecture, with event-sourced state, seed streams, replay,
checkpoint/resume and public conformance suites.

*Roadmap (not yet shipped).* A Bayesian-optimization driver and a
hidden-dataset oracle for retrospective studies; a statistics module for
paired driver comparisons; asynchronous executors and a Slurm-backed
workflow resource behind the frozen Resource lifecycle (ADR-0007);
declarative constraint vocabulary for specs; plugin entry points for
external drivers and oracles. See `ARCHITECTURE.md` and
`docs/adr/` for boundaries and decision gates.
