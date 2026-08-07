"""Every shipped participant passes the public conformance suites."""

from __future__ import annotations

import pytest

from cadaques import Action, Cost, Query
from cadaques.drivers import AnnealedLocalDriver, RandomDriver
from cadaques.oracles import AnalyticOracle, Ising2DOracle, quadratic_bowl
from cadaques.protocols import OracleResource
from cadaques.testing import (
    check_driver_contract,
    check_oracle_contract,
    check_resource_contract,
)

SPACE = {"T": (1.0, 4.0)}
QUERY = Query(params={"T": 2.0}, fidelity={"L": 6, "sweeps": 10, "equilibration": 5})


def analytic():
    return AnalyticOracle(fn=quadratic_bowl({"T": 2.269}),
                          price_fn=lambda _q: Cost(seconds=1.0))


ORACLES = {
    "analytic": analytic,
    "ising": lambda: Ising2DOracle(default_L=6, default_sweeps=10,
                                   default_equilibration=5, seed=0),
}

DRIVERS = {
    "random": lambda: RandomDriver(space=SPACE, seed=0),
    "annealed": lambda: AnnealedLocalDriver(space=SPACE, seed=0),
}

RESOURCES = {
    "oracle_adapter_analytic": lambda: OracleResource(analytic()),
    "oracle_adapter_ising": lambda: OracleResource(ORACLES["ising"]()),
}


@pytest.mark.parametrize("name", ORACLES)
def test_oracle_contract(name):
    check_oracle_contract(ORACLES[name], QUERY)


@pytest.mark.parametrize("name", DRIVERS)
def test_driver_contract(name):
    check_driver_contract(DRIVERS[name], SPACE)


@pytest.mark.parametrize("name", RESOURCES)
def test_resource_contract(name):
    check_resource_contract(
        RESOURCES[name],
        Action(operation="evaluate", parameters={"T": 2.0},
               fidelity={"L": 6, "sweeps": 10, "equilibration": 5}),
    )
