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
