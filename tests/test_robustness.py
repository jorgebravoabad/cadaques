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


class TestReadersNeverSeeTornFiles:
    """Read-only tools take no write lock; a torn file used to surface as internal_error."""

    def _torn_spec_while_locked(self, tmp_path, hold=0.25):
        """A writer (as 0.4.0 was) truncates spec.json under the lock, then repairs it."""
        import threading
        import time

        tb = Toolbox(tmp_path / "ws")
        assert call(tb, "create_campaign", "mk", spec=spec())["ok"]
        assert call(tb, "reserve_evaluation", "r1", params={"x": 0.5})["ok"]
        path = tmp_path / "ws" / "c" / "spec.json"
        good = path.read_bytes()
        torn, done = threading.Event(), threading.Event()

        def writer():
            with tb.store.lock("c"):
                path.write_bytes(b"")
                torn.set()
                time.sleep(hold)
                path.write_bytes(good)
            done.set()

        thread = threading.Thread(target=writer)
        thread.start()
        assert torn.wait(5)
        return thread, done

    def test_status_waits_out_a_writer_instead_of_failing(self, tmp_path):
        thread, done = self._torn_spec_while_locked(tmp_path)
        reader = Toolbox(tmp_path / "ws")  # cold: no cache, may not trust anything
        r = reader.call("get_campaign_status", {"campaign_id": "c"})
        thread.join()
        assert r["ok"], r
        assert done.is_set() and len(r["result"]["pending"]) == 1

    def test_listing_waits_out_a_writer_instead_of_reporting_unreadable(self, tmp_path):
        thread, _ = self._torn_spec_while_locked(tmp_path)
        r = Toolbox(tmp_path / "ws").call("list_campaigns", {})
        thread.join()
        assert r["ok"] and r["result"]["campaigns"][0]["readable"] is True

    def test_events_waits_out_a_writer_instead_of_failing(self, tmp_path):
        thread, _ = self._torn_spec_while_locked(tmp_path)
        r = Toolbox(tmp_path / "ws").call("get_events", {"campaign_id": "c"})
        thread.join()
        assert r["ok"] and r["result"]["total_events"] == 3

    def test_a_really_damaged_campaign_still_reports_an_error(self, tmp_path):
        tb = Toolbox(tmp_path / "ws")
        assert call(tb, "create_campaign", "mk", spec=spec())["ok"]
        (tmp_path / "ws" / "c" / "spec.json").write_bytes(b"")
        r = Toolbox(tmp_path / "ws").call("get_events", {"campaign_id": "c"})
        assert not r["ok"] and r["error"]["code"] == "internal_error"

    def test_readers_and_writers_hammering_one_campaign_never_error(self, tmp_path):
        import threading
        import time

        tb = Toolbox(tmp_path / "ws")
        assert call(tb, "create_campaign", "mk", spec=spec(seconds=1e9, tariff=1.0))["ok"]
        stop, bad, reads = time.monotonic() + 1.0, [], [0]

        def writer(k):
            box, i = Toolbox(tmp_path / "ws"), 0
            while time.monotonic() < stop:
                i += 1
                r = call(box, "reserve_evaluation", f"w{k}-{i}", params={"x": 0.5})
                if r["ok"]:
                    call(box, "settle_evaluation", f"w{k}-s{i}", settled_cost={"seconds": 1.0},
                         evaluation_id=r["result"]["evaluation_id"], outcome="completed", value=1.0)

        def reader():
            box = Toolbox(tmp_path / "ws")
            while time.monotonic() < stop:
                for name, args in (("get_campaign_status", {"campaign_id": "c"}),
                                   ("list_campaigns", {}),
                                   ("get_events", {"campaign_id": "c", "limit": 5})):
                    r = box.call(name, args)
                    reads[0] += 1
                    if not r["ok"] or (name == "list_campaigns" and not r["result"]["campaigns"][0]["readable"]):
                        bad.append(r)

        threads = [threading.Thread(target=writer, args=(k,)) for k in range(2)]
        threads += [threading.Thread(target=reader) for _ in range(2)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert reads[0] > 20 and not bad, bad[:2]


class TestTornJournalTail:
    """A crash mid-append left half a line in requests.jsonl; every later call then raised."""

    def _campaign(self, tmp_path):
        tb = Toolbox(tmp_path / "ws")
        assert call(tb, "create_campaign", "mk", spec=spec(seconds=100.0))["ok"]
        first = call(tb, "reserve_evaluation", "r1", params={"x": 0.5})
        assert first["ok"]
        return first, tmp_path / "ws" / "c" / "requests.jsonl"

    def _tear(self, journal, keep=0.5):
        lines = journal.read_bytes().splitlines(keepends=True)
        last = lines[-1]
        journal.write_bytes(b"".join(lines[:-1]) + last[: int(len(last) * keep)])

    def test_later_calls_survive_a_truncated_final_line(self, tmp_path):
        first, journal = self._campaign(tmp_path)
        second = call(Toolbox(tmp_path / "ws"), "reserve_evaluation", "r2", params={"x": 0.6})
        assert second["ok"]
        self._tear(journal)  # the entry of r2 is cut in half
        fresh = Toolbox(tmp_path / "ws")
        third = call(fresh, "reserve_evaluation", "r3", params={"x": 0.7})
        assert third["ok"], third
        again = call(Toolbox(tmp_path / "ws"), "reserve_evaluation", "r1", params={"x": 0.5})
        assert again["replayed"] and again["result"]["evaluation_id"] == first["result"]["evaluation_id"]
        assert journal.read_bytes().endswith(b"\n")
        for line in journal.read_bytes().splitlines():
            json.loads(line)  # the torn fragment is gone, not glued into a garbage line

    def test_the_retry_of_the_torn_request_is_reconstructed_from_the_events(self, tmp_path):
        _, journal = self._campaign(tmp_path)
        second = call(Toolbox(tmp_path / "ws"), "reserve_evaluation", "r2", params={"x": 0.6})
        self._tear(journal)
        retry = call(Toolbox(tmp_path / "ws"), "reserve_evaluation", "r2", params={"x": 0.6})
        assert retry["ok"] and retry["reconstructed"]
        assert retry["result"]["evaluation_id"] == second["result"]["evaluation_id"]
        assert call(Toolbox(tmp_path / "ws"), "reserve_evaluation", "r4", params={"x": 0.1})["ok"]

    def test_a_garbage_line_written_by_an_older_release_is_ignored(self, tmp_path):
        """0.4.0 glued the next entry after the torn fragment with a newline."""
        first, journal = self._campaign(tmp_path)
        with journal.open("ab") as fh:
            fh.write(b'{"schema": "cadaques.requests/1", "request_id": "ghost", "tool": "rese\n')
        tb = Toolbox(tmp_path / "ws")
        assert call(tb, "reserve_evaluation", "r2", params={"x": 0.6})["ok"]
        assert call(Toolbox(tmp_path / "ws"), "reserve_evaluation", "r1", params={"x": 0.5})["replayed"]
        assert call(Toolbox(tmp_path / "ws"), "reserve_evaluation", "r2", params={"x": 0.6})["replayed"]
        assert call(Toolbox(tmp_path / "ws"), "reserve_evaluation", "r5", params={"x": 0.2})["ok"]
        # the id of the damaged line is simply unknown: the call is executed, not an error
        assert call(Toolbox(tmp_path / "ws"), "reserve_evaluation", "ghost", params={"x": 0.3})["ok"]

    def test_an_entry_that_only_lacks_its_newline_is_kept(self, tmp_path):
        first, journal = self._campaign(tmp_path)
        journal.write_bytes(journal.read_bytes().rstrip(b"\n"))
        assert call(Toolbox(tmp_path / "ws"), "reserve_evaluation", "r2", params={"x": 0.6})["ok"]
        again = call(Toolbox(tmp_path / "ws"), "reserve_evaluation", "r1", params={"x": 0.5})
        assert again["replayed"] and not again.get("reconstructed")
