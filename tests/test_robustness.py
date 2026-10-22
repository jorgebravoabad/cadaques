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


class TestSettledCostIsRequired:
    """settle_evaluation used to charge the declared cost when settled_cost was omitted."""

    def _pending(self, tmp_path):
        tb = Toolbox(tmp_path / "ws")
        assert call(tb, "create_campaign", "mk", spec=spec(seconds=100.0))["ok"]
        r = call(tb, "reserve_evaluation", "r1", params={"x": 0.5})
        return tb, r["result"]["evaluation_id"]

    def test_omission_is_refused_with_a_helpful_message(self, tmp_path):
        tb, ev = self._pending(tmp_path)
        before = tb.call("get_campaign_status", {"campaign_id": "c"})["result"]
        r = call(tb, "settle_evaluation", "s1", evaluation_id=ev, outcome="completed", value=1.0)
        assert not r["ok"] and r["error"]["code"] == "invalid_arguments"
        assert "settled_cost" in r["error"]["message"] and "actually cost" in r["error"]["message"]
        assert r["error"]["details"]["field"] == "settled_cost"
        assert r["error"]["next_actions"] == ["settle_evaluation"]
        after = tb.call("get_campaign_status", {"campaign_id": "c"})["result"]
        assert after["n_events"] == before["n_events"] and after["budget"] == before["budget"]
        assert [p["evaluation_id"] for p in after["pending"]] == [ev]  # still open

    def test_failed_outcomes_must_report_their_cost_too(self, tmp_path):
        tb, ev = self._pending(tmp_path)
        r = call(tb, "settle_evaluation", "s1", evaluation_id=ev, outcome="failed")
        assert r["error"]["code"] == "invalid_arguments" and "settled_cost" in r["error"]["message"]

    def test_the_handler_does_not_default_even_if_the_schema_is_bypassed(self, tmp_path):
        from cadaques.agent import tools as tools_mod

        tb, ev = self._pending(tmp_path)
        call_ctx = tools_mod._Call(tb, {"campaign_id": "c", "evaluation_id": ev, "outcome": "completed",
                                         "value": 1.0, "actor": AGENT, "request_id": "s1"})
        with pytest.raises(tools_mod.ToolError) as err:
            tools_mod._settle_evaluation(call_ctx)
        assert err.value.code == "invalid_arguments"

    def test_the_schema_declares_it_required(self, tmp_path):
        schema = next(t for t in Toolbox(tmp_path / "ws").schemas() if t["name"] == "settle_evaluation")
        assert "settled_cost" in schema["input_schema"]["required"]

    def test_an_explicit_value_is_recorded_as_reported_and_an_empty_cost_is_a_free_evaluation(self, tmp_path):
        tb, ev = self._pending(tmp_path)
        r = call(tb, "settle_evaluation", "s1", evaluation_id=ev, outcome="completed", value=1.0,
                 settled_cost={})
        assert r["ok"] and r["result"]["settled_source"] == "reported"
        assert r["result"]["status"]["budget"]["spent"]["seconds"] == 0.0


class Crash(BaseException):
    """Stand-in for the process dying: nothing in the kernel catches BaseException."""


def _lose_the_journal_entry(monkeypatch):
    """Make the next journal_append capture its response and then 'kill' the process."""
    from cadaques.agent import store as store_mod

    captured = {}

    def die(self, campaign_id, request_id, tool, digest, response):
        captured["response"] = response
        raise Crash

    monkeypatch.setattr(store_mod.CampaignStore, "journal_append", die)
    return captured


def _shape(response):
    """A response without what legitimately differs on a retry (flags, clocks)."""
    out = json.loads(json.dumps(response))
    for flag in ("replayed", "reconstructed"):
        out.pop(flag, None)
    for status in _walk(out, "status"):
        for pending in status.get("pending", []):
            pending.pop("age_seconds", None)
    return out


def _walk(node, key):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key and isinstance(v, dict):
                yield v
            yield from _walk(v, key)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v, key)


def _ising():
    return {
        "oracle": {"class": "cadaques.oracles.ising.Ising2DOracle",
                   "params": {"default_L": 4, "default_sweeps": 5, "default_equilibration": 2}},
        "driver": {"class": "cadaques.drivers.reference.RandomDriver",
                   "params": {"space": {"T": [1.5, 3.5]}}},
        "budget_total": {"seconds": 1000.0},
        "task": {"bounds": {"T": [1.5, 3.5]}, "direction": "maximize", "fidelities": {},
                 "success_value": None, "name": "ising"},
        "seed": 1,
    }


def _prep_empty(tb):
    call(tb, "create_campaign", "p0", spec=spec())


def _prep_one(tb):
    _prep_empty(tb)
    call(tb, "reserve_evaluation", "p1", params={"x": 0.5})


def _prep_full(tb):  # 6 of 7 seconds reserved
    _prep_one(tb)
    call(tb, "reserve_evaluation", "p2", params={"x": 0.6})


def _prep_spent(tb):  # one evaluation settled at 6 s: nothing left, nothing pending
    _prep_one(tb)
    call(tb, "settle_evaluation", "p2", evaluation_id="ev-000002", outcome="completed",
         value=1.0, settled_cost={"seconds": 6.0})


def _prep_ising(tb):
    call(tb, "create_campaign", "p0", spec=_ising())


SCENARIOS = {
    "create": (lambda tb: None, "create_campaign", dict(spec=spec()), "created"),
    "reserve_ok": (_prep_empty, "reserve_evaluation",
                   dict(params={"x": 0.4}, decision_cost={"seconds": 0.25}), "evaluation_id"),
    "reserve_refused_with_pending": (_prep_full, "reserve_evaluation",
                                     dict(params={"x": 0.4}, decision_cost={"seconds": 0.25}),
                                     "budget_exhausted"),
    "reserve_refused_and_campaign_stops": (_prep_spent, "reserve_evaluation",
                                           dict(params={"x": 0.4}, decision_cost={"seconds": 0.25}),
                                           "budget_exhausted"),
    "reserve_invalid_query": (_prep_empty, "reserve_evaluation",
                              dict(params={"x": 7.0}, decision_cost={"seconds": 0.25}),
                              "invalid_query"),
    "settle_completed": (_prep_one, "settle_evaluation",
                         dict(evaluation_id="ev-000002", outcome="completed", value=2.0,
                              settled_cost={"seconds": 4.0}), "settled_cost"),
    "settle_failed": (_prep_one, "settle_evaluation",
                      dict(evaluation_id="ev-000002", outcome="failed",
                           settled_cost={"seconds": 1.0}), "settled_cost"),
    "cancel": (_prep_one, "cancel_evaluation", dict(evaluation_id="ev-000002"), "cancelled"),
    "stop": (_prep_one, "stop_campaign", dict(reason="enough"), "stopped"),
    "run": (_prep_ising, "run_campaign", dict(max_new_queries=3), "n_new_queries"),
}


class TestRetryAfterACrashBetweenCommitAndJournal:
    """A retry must get the original answer: same shape for success, the same refusal for a refusal."""

    @pytest.mark.parametrize("scenario", sorted(SCENARIOS))
    def test_the_retry_returns_the_original_response(self, tmp_path, monkeypatch, scenario):
        prep, tool, args, marker = SCENARIOS[scenario]
        tb = Toolbox(tmp_path / "ws")
        prep(tb)
        with monkeypatch.context() as m:
            captured = _lose_the_journal_entry(m)
            with pytest.raises(Crash):
                call(tb, tool, "T", **args)
        original = captured["response"]
        retry = call(Toolbox(tmp_path / "ws"), tool, "T", **args)
        assert retry["replayed"] and retry["reconstructed"]
        assert retry["ok"] == original["ok"]
        if not original["ok"]:
            assert retry["error"]["code"] == marker
        elif scenario != "run":  # wall-clock Ising costs: shape only
            assert marker in retry["result"]
        if scenario != "run":
            assert _shape(retry) == _shape(original)
        else:
            assert set(retry["result"]) == set(original["result"])
            assert retry["result"]["n_new_queries"] == original["result"]["n_new_queries"]
            assert retry["result"]["stop_reason"] == original["result"]["stop_reason"]

    @pytest.mark.parametrize("scenario", ["reserve_refused_with_pending",
                                          "reserve_refused_and_campaign_stops",
                                          "reserve_invalid_query"])
    def test_a_refusal_is_never_reconstructed_as_success(self, tmp_path, monkeypatch, scenario):
        prep, tool, args, marker = SCENARIOS[scenario]
        tb = Toolbox(tmp_path / "ws")
        prep(tb)
        with monkeypatch.context() as m:
            _lose_the_journal_entry(m)
            with pytest.raises(Crash):
                call(tb, tool, "T", **args)
        n_events = tb.call("get_campaign_status", {"campaign_id": "c"})["result"]["n_events"]
        retry = call(Toolbox(tmp_path / "ws"), tool, "T", **args)
        assert not retry["ok"] and retry["error"]["code"] == marker
        assert "evaluation_id" not in retry.get("result", {})
        after = Toolbox(tmp_path / "ws").call("get_campaign_status", {"campaign_id": "c"})["result"]
        assert after["n_events"] == n_events  # the retry did not act a second time

    def test_a_committed_refusal_is_journaled_like_any_other_response(self, tmp_path):
        tb = Toolbox(tmp_path / "ws")
        _prep_full(tb)
        first = call(tb, "reserve_evaluation", "T", params={"x": 0.4}, decision_cost={"seconds": 0.25})
        again = call(Toolbox(tmp_path / "ws"), "reserve_evaluation", "T", params={"x": 0.4},
                     decision_cost={"seconds": 0.25})
        assert again["replayed"] and "reconstructed" not in again
        assert _shape(again) == _shape(first) and first["error"]["code"] == "budget_exhausted"

    def test_records_from_before_the_fix_still_give_a_refusal_not_a_success(self, tmp_path, monkeypatch):
        """0.4.0 wrote no answer with the events; the refusal is inferred from them."""
        tb = Toolbox(tmp_path / "ws")
        _prep_full(tb)
        with monkeypatch.context() as m:
            _lose_the_journal_entry(m)
            with pytest.raises(Crash):
                call(tb, "reserve_evaluation", "T", params={"x": 0.4}, decision_cost={"seconds": 0.25})
        log = tmp_path / "ws" / "c" / "events.jsonl"
        lines = [json.loads(line) for line in log.read_text().splitlines()]
        for line in lines:
            line["payload"].pop("refusal", None)
        log.write_text("".join(json.dumps(line) + "\n" for line in lines))
        retry = call(Toolbox(tmp_path / "ws"), "reserve_evaluation", "T", params={"x": 0.4},
                     decision_cost={"seconds": 0.25})
        assert not retry["ok"] and retry["error"]["code"] == "budget_exhausted"
        assert retry["reconstructed"]
