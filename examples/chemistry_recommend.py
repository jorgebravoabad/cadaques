"""The first vertical, end to end: a reaction CSV in, ranked experiments out.

A chemist's table — composition descriptors, reaction conditions, a
target property, a cost per run — becomes, with one role declaration:
(a) a ranked, reproducible, provenanced recommendation for the next
experiments, and (b) a budgeted retrospective campaign for method
comparison. Run:

    pip install "cadaques[bo]"
    python examples/chemistry_recommend.py
"""

from pathlib import Path

from cadaques import Budget, Cost
from cadaques.drivers import BayesianDriver, RandomDriver
from cadaques.stats import paired_campaigns
from cadaques.verticals import ReactionTable

HERE = Path(__file__).parent

# --- a small synthetic OCM-style table (replace with your CSV) -----------
ROWS = [
    {"x_Mn": 0.10, "x_Ce": 0.05, "T": 700, "P": 1.0, "c2_yield": 0.18, "run_s": 3600},
    {"x_Mn": 0.20, "x_Ce": 0.05, "T": 750, "P": 1.0, "c2_yield": 0.29, "run_s": 3650},
    {"x_Mn": 0.20, "x_Ce": 0.10, "T": 800, "P": 1.5, "c2_yield": 0.43, "run_s": 3800},
    {"x_Mn": 0.25, "x_Ce": 0.10, "T": 820, "P": 1.6, "c2_yield": 0.47, "run_s": 3850},
    {"x_Mn": 0.30, "x_Ce": 0.15, "T": 850, "P": 2.0, "c2_yield": 0.44, "run_s": 4000},
    {"x_Mn": 0.15, "x_Ce": 0.20, "T": 760, "P": 1.8, "c2_yield": 0.33, "run_s": 3700},
    {"x_Mn": 0.28, "x_Ce": 0.08, "T": 840, "P": 1.4, "c2_yield": 0.45, "run_s": 3900},
]

table = ReactionTable(
    rows=ROWS,
    objective="c2_yield",
    composition=("x_Mn", "x_Ce"),
    conditions=("T", "P"),
    cost_columns={"seconds": "run_s"},
    tariff={"seconds": 3800.0},
    name="ocm_demo",
)

# --- (a) recommendation mode: what should the lab try next? ---------------
ranking = table.recommend_next(k=5, seed=42)
print(ranking.summary())
out_csv = HERE / "next_experiments.csv"
ranking.to_csv(out_csv)
print(f"\nExported for the lab notebook: {out_csv}")

# --- (b) campaign mode: would BO have beaten random on this table? ---------
def make(driver_cls):
    def factory(seed: int):
        t = ReactionTable(**{**table.__dict__, "tolerance": 0.8})
        drv = (BayesianDriver(space=t.oracle().bounds, n_initial=3)
               if driver_cls is BayesianDriver
               else RandomDriver(space=t.oracle().bounds))
        return t.campaign(drv, Budget(total=Cost(seconds=25000.0)),
                          seed=seed, meter_driver=False)
    return factory

comparison = paired_campaigns(
    make(BayesianDriver), make(RandomDriver),
    seeds=range(8),
    metric=lambda o: o.best.value,
    metric_name="best c2_yield under equal budget",
    alternative="greater",
)
print("\n" + comparison.summary())
