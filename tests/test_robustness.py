"""Release 0.4.1 hardening: defects found by the kernel validation, one class per defect.

Each test failed on 0.4.0 / the ADR-0015 branch and states the guarantee the
fix restores.
"""

from __future__ import annotations

import json
import os

import pytest

from cadaques.agent import Toolbox
from cadaques.core.spec import CampaignSpec

AGENT = {"kind": "agent", "id": "robust", "model": "test-model", "version": "1"}
BOUNDS = {"x": [0.0, 1.0]}


def spec(seconds=7.0, tariff=3.0, driver="external"):
    drivers = {
        "external": {"class": "cadaques.drivers.external.ExternalDriver", "params": {}},
        "random": {"class": "cadaques.drivers.reference.RandomDriver", "params": {"space": BOUNDS}},
    }
    return {
        "oracle": {"class": "cadaques.oracles.external.ExternalOracle",
                   "params": {"tariff": {"seconds": tariff}}},
        "driver": drivers[driver],
        "budget_total": {"seconds": seconds},
        "task": {"bounds": BOUNDS, "direction": "maximize", "fidelities": {},
                 "success_value": None, "name": "t"},
        "seed": 3,
    }


def call(tb, tool, request_id, **args):
    args.setdefault("campaign_id", "c")
    return tb.call(tool, {"actor": AGENT, "request_id": request_id, **args})


class TestSpecWriteIsAtomic:
    """A SIGKILL during the spec write left a 0-byte spec.json that bricked the campaign."""

    def test_a_crash_while_writing_the_spec_leaves_no_campaign_rather_than_a_broken_one(
        self, tmp_path, monkeypatch
    ):
        real = os.replace

        def die(src, dst, *a, **k):
            if os.path.basename(dst) == "spec.json":
                raise KeyboardInterrupt
            return real(src, dst, *a, **k)

        tb = Toolbox(tmp_path / "ws")
        monkeypatch.setattr(os, "replace", die)
        with pytest.raises(KeyboardInterrupt):
            call(tb, "create_campaign", "mk", spec=spec())
        monkeypatch.undo()

        directory = tmp_path / "ws" / "c"
        assert not (directory / "spec.json").exists() or (directory / "spec.json").stat().st_size > 0
        fresh = Toolbox(tmp_path / "ws")
        assert fresh.call("get_campaign_status", {"campaign_id": "c"})["error"]["code"] == "unknown_campaign"
        assert call(fresh, "create_campaign", "mk", spec=spec())["ok"]  # the retry succeeds
        assert call(fresh, "reserve_evaluation", "r1", params={"x": 0.5})["ok"]
        CampaignSpec.load(directory / "spec.json")
