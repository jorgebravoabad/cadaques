Quickstart
==========

A discovery campaign with an exact answer: locate the critical
temperature of the 2D Ising model by maximizing the magnetic
susceptibility under a fixed compute budget. Onsager's exact result,
:math:`T_c = 2/\ln(1+\sqrt{2}) \approx 2.269`, provides the ground
truth against which any driver can be validated.

.. code-block:: python

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

The campaign ends when the budget is exhausted — not when an iteration
counter runs out — and every transaction (declared and settled cost,
timestamped) is recorded in the :class:`~cadaques.Ledger`, exportable
to JSONL as a complete, replayable trace.


The campaign is a durable object (since 0.2)
--------------------------------------------

A campaign is not a script you ran once — it is a declarative,
replayable object:

.. code-block:: python

   from cadaques import Budget, Campaign, Cost, Task, checkpoint, replay, resume
   from cadaques.drivers import RandomDriver
   from cadaques.oracles import Ising2DOracle

   task = Task.from_bounds({"T": (1.5, 3.5)}, name="ising_tc")
   campaign = Campaign(
       Ising2DOracle(), RandomDriver(space=task.space.bounds),
       Budget(total=Cost(seconds=30.0)), task=task, seed=42,
   )
   spec = campaign.to_spec()             # the campaign as portable JSON
   outcome = campaign.run()
   outcome.events.to_jsonl("run.jsonl")  # the event log: source of truth

   assert replay(spec, "run.jsonl") == outcome.state   # audit, no re-execution

From a spreadsheet to the next experiments (0.3)
------------------------------------------------

Declare column roles once and a measured reaction table compiles to
kernel objects — then ask which experiments the lab should run next:

.. code-block:: python

   from cadaques.verticals import ReactionTable

   table = ReactionTable.from_csv(
       "reactions.csv",
       objective="c2_yield",
       composition=("x_Mn", "x_Ce"),
       conditions=("T", "P"),
       cost_columns={"seconds": "run_s"},
   )
   ranking = table.recommend_next(k=5, seed=42)   # requires cadaques[bo]
   print(ranking.summary())                       # value, uncertainty, cost,
   ranking.to_csv("next_experiments.csv")         # and metered thinking time

The returned ranking is seeded (bit-for-bit reproducible), carries full
provenance (driver declaration, history fingerprint), and meters the
wall time spent thinking — the price of intelligence, attached to the
advice it produced. See :mod:`cadaques.runtime.recommend`.
