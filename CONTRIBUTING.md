# Contributing to CADAQUES

Thanks for your interest. This is a **single-maintainer research project**
(Jorge Bravo-Abad, UAM). I aim to respond to issues and pull requests
within about two weeks — slower near teaching and paper deadlines. Every
rule below is already enforced on the maintainer's own commits; nothing
here is aspirational.

## Before you write code

Open an **issue first** for anything beyond a typo or small docs fix.
It saves you from building something that is gated (see *Scope*, below)
and saves both of us a review round.

## Development setup

```bash
git clone https://github.com/jorgebravoabad/cadaques.git
cd cadaques
pip install -e ".[dev,bo]"
python -m pytest tests/ -q     # must pass in full before and after your change
```

## The bar for a pull request

1. **Tests.** The full suite passes; new behavior comes with new tests.
   CI runs on every push and must be green.
2. **Conformance.** A contributed Oracle, Driver, or Resource must pass
   the public suites in `cadaques.testing`
   (`check_oracle_contract`, `check_driver_contract`,
   `check_resource_contract`). This is the objective bar — style
   discussion comes after, not instead.
3. **Design changes engage the ADRs.** Durable decisions live in
   `docs/adr/`. If your change touches one, reference it; if it needs a
   new decision, propose the ADR in the PR. "The precedent says X"
   is an expected — and answerable — review comment.
4. **Two invariants are constitutional** and have veto power over any
   feature (ADR-0006): every query is priced *ex ante* and settled
   *ex post*, with the discrepancy recorded; and both sides of the loop
   are metered. A change that lets any query or decision escape the
   ledger will not merge, however useful otherwise.
5. **Dependencies are design decisions.** Core stays NumPy-only
   (ADR-0008). Heavy stacks go behind optional extras (like
   `cadaques[bo]`) or external plugin packages. Proposing a dependency
   means proposing an ADR.
6. **Diffs must be readable.** Small, focused PRs get reviewed quickly;
   large ones will be asked to split. Do not reformat lines you are not
   changing. Every line of every PR is read before merging — if it is
   too big to read, it is too big to merge.
7. **Compatibility.** Nothing public breaks without a deprecation shim
   spanning at least two minor releases (ADR-0010). The tag
   `v0.1.0-paper` is permanent; its imports must keep working.

Style: `ruff` clean, type hints on public interfaces, frozen dataclasses
for record types, docstrings that state ownership boundaries ("must not
own") in the spirit of `ARCHITECTURE.md`.

## Scope: what is welcome now

Examples and documentation; bug fixes with a failing test; a
`ReactionTable` recipe for a public dataset; protocol-conformant Oracle
or Driver adapters (passing the suites); improvements to
`cadaques.stats` with validation against a reference implementation.

**Gated for now** (see `ARCHITECTURE.md` and the decision gates in
`docs/adr/`): asynchronous executors and schedulers (ADR-0007), an LLM
agent driver, laboratory integrations, a plugin marketplace, and any
schema-migration machinery. Proposals are welcome as issues; code ahead
of the gate will be parked, not merged.

## Access and licensing

Contributions do not require — and do not confer — write access to the
repository; long-standing contributors remain contributors. By
submitting a contribution you agree it is licensed under the project's
MIT license.
