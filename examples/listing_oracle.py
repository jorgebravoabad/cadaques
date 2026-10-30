# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Jorge Bravo-Abad
"""Listing 1 of the SoftwareX article: a user's own simulation as an Oracle, run by the shipped GP-EI driver.

    pip install 'cadaques[bo]' && python examples/listing_oracle.py
my_code stands for the user's simulation (here a truncated Fourier series, as in the article).
Counts vary from run to run because planning is metered by wall time; replay of the log is exact.
"""
# ---- quoted in the article from here
import math, time
from cadaques import Budget, Campaign, Cost, Result
from cadaques.drivers import BayesianDriver     # pip install 'cadaques[bo]'

class MySimulation:                  # an Oracle: two methods, no base class
    def price(self, query):          # declared cost, fixed before it runs
        return Cost(seconds=1e-7 * query.fidelity["n"])
    def evaluate(self, query):       # settled cost, measured after it
        t0 = time.perf_counter()
        x, n = query.params["x"], query.fidelity["n"]
        value = sum(math.sin(k * x) / k for k in range(1, n + 1))
        cost = Cost(seconds=time.perf_counter() - t0)
        return Result(query, value, cost)

campaign = Campaign(
    MySimulation(),
    BayesianDriver(space={"x": [0.0, 1.0]}, fidelity={"n": 300000}),
    Budget(total=Cost(seconds=2.0)), seed=0)
outcome = campaign.run()             # until no declared cost fits the budget

d = sum(t.settled.seconds for t in outcome.ledger.transactions if t.kind == "driver")
print(f"stop: {outcome.stop_reason} | evaluations: {outcome.n_queries} | spent: {outcome.budget.spent.seconds:.3f} s "
      f"of 2 s | planning share: {d / outcome.budget.spent.seconds:.1%}")
