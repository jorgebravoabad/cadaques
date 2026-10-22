"""A new process starts from the persisted snapshot, not from a replay (ADR-0016).

The event log stays the source of truth. A snapshot and a request index are
caches: a new process uses them only after proving them against the log, folds
forward what was appended since, and replays the log on any doubt.
"""

from __future__ import annotations

import json
import shutil

import pytest

from cadaques import EventLog
from cadaques.agent import Toolbox
from cadaques.agent import store as store_mod
from cadaques.agent.store import INDEX_EVERY, CampaignStore
from cadaques.core.events import read_events_from
from cadaques.runtime.replay import resume
from cadaques.runtime.state import reduce

AGENT = {"kind": "agent", "id": "np", "model": "test-model", "version": "1"}
HUMAN = {"kind": "human", "id": "jorge"}
BOUNDS = {"x": [0.0, 1.0]}


def spec(driver="external", seconds=1e9):
    drivers = {
        "external": {"class": "cadaques.drivers.external.ExternalDriver", "params": {}},
        "random": {"class": "cadaques.drivers.reference.RandomDriver", "params": {"space": BOUNDS}},
    }
    return {
        "oracle": {"class": "cadaques.oracles.external.ExternalOracle",
                   "params": {"tariff": {"seconds": 1.0}}},
        "driver": drivers[driver],
        "budget_total": {"seconds": seconds},
        "task": {"bounds": BOUNDS, "direction": "maximize", "fidelities": {},
                 "success_value": None, "name": "t"},
        "seed": 3,
    }


def call(tb, tool, request_id=None, **args):
    if tool not in ("get_campaign_status", "get_events", "list_campaigns"):
        args = {"actor": AGENT, "request_id": request_id, **args}
    args.setdefault("campaign_id", "c")
    return tb.call(tool, args)


def step(tb, i, *, settle=True, cost=1.0, actor=AGENT):
    r = tb.call("reserve_evaluation", {"campaign_id": "c", "actor": actor, "request_id": f"r{i}",
                                       "params": {"x": (i % 100) / 100.0}})
    assert r["ok"], r
    ev = r["result"]["evaluation_id"]
    if settle:
        s = tb.call("settle_evaluation", {
            "campaign_id": "c", "actor": actor, "request_id": f"s{i}", "evaluation_id": ev,
            "outcome": "completed", "value": float(i % 7), "settled_cost": {"seconds": cost}})
        assert s["ok"], s
    return ev


def make(tmp_path, n=0, driver="external"):
    tb = Toolbox(tmp_path / "ws")
    assert call(tb, "create_campaign", "create", spec=spec(driver))["ok"]
    for i in range(n):
        step(tb, i)
    return tb


def directory(tmp_path):
    return tmp_path / "ws" / "c"


def status(tb):
    s = tb.call("get_campaign_status", {"campaign_id": "c"})
    assert s["ok"], s
    s = s["result"]
    for p in s["pending"]:
        p.pop("age_seconds")
    return s


def normalized(path):
    """The log without wall-clock fields."""
    out = []
    for e in EventLog.from_jsonl(path):
        d = e.as_dict()
        d.pop("timestamp")
        d["payload"].pop("elapsed_since_reservation", None)
        for p in d["payload"].values():
            if isinstance(p, dict):
                p.pop("age_seconds", None)
        out.append(d)
    return out


class Spy:
    """Records how a store reads the log and which fallbacks it takes."""

    def __init__(self, monkeypatch):
        self.offsets, self.cold, self.light = [], 0, 0
        real_read, real_cold, real_light = (store_mod.read_events_from, CampaignStore._cold,
                                            CampaignStore._light)

        def read(path, offset=0, *a, **k):
            if str(path).endswith("events.jsonl"):
                self.offsets.append(offset)
            return real_read(path, offset, *a, **k)

        def cold(store, *a, **k):
            self.cold += 1
            return real_cold(store, *a, **k)

        def light(store, *a, **k):
            self.light += 1
            return real_light(store, *a, **k)

        monkeypatch.setattr(store_mod, "read_events_from", read)
        monkeypatch.setattr(CampaignStore, "_cold", cold)
        monkeypatch.setattr(CampaignStore, "_light", light)

    @property
    def read_the_whole_log(self):
        return 0 in self.offsets


class TestNewProcessDoesNotReplay:
    def test_first_mutating_call_reads_only_the_tail(self, tmp_path, monkeypatch):
        make(tmp_path, 300)
        spy = Spy(monkeypatch)
        step(Toolbox(tmp_path / "ws"), 1000)  # a new process: reserve + settle
        assert spy.cold == 0 and spy.light == 1
        assert not spy.read_the_whole_log
        size = (directory(tmp_path) / "events.jsonl").stat().st_size
        assert max(spy.offsets) > 0.8 * size  # it only looked at the last few events

    def test_the_journal_is_not_scanned_for_a_request_id_the_record_has_never_seen(
        self, tmp_path, monkeypatch
    ):
        make(tmp_path, 100)
        scanned = []
        real = CampaignStore._journal_index
        monkeypatch.setattr(CampaignStore, "_journal_index",
                            lambda store, *a, **k: scanned.append(a) or real(store, *a, **k))
        step(Toolbox(tmp_path / "ws"), 1000)
        assert scanned == []

    def test_a_cold_status_and_listing_still_do_not_replay(self, tmp_path, monkeypatch):
        make(tmp_path, 50)
        spy = Spy(monkeypatch)
        tb = Toolbox(tmp_path / "ws")
        status(tb)
        assert tb.call("list_campaigns", {})["ok"]
        assert spy.cold == 0 and not spy.read_the_whole_log

    def test_every_call_in_a_new_process_per_call_run_builds_the_same_record(self, tmp_path):
        """The command-line pattern: one process per call."""
        warm = make(tmp_path / "warm", 0)
        for i in range(90):
            step(warm, i)
        cold_root = tmp_path / "cold"
        make(cold_root, 0)
        for i in range(90):
            step(Toolbox(cold_root / "ws"), i)  # a fresh Toolbox per round trip
        assert normalized(directory(cold_root) / "events.jsonl") == normalized(
            directory(tmp_path / "warm") / "events.jsonl")
        assert status(Toolbox(cold_root / "ws")) == status(warm)

    def test_the_record_resumes_and_replays_to_the_same_state(self, tmp_path):
        make(tmp_path, 40)
        for i in range(40, 80):
            step(Toolbox(tmp_path / "ws"), i)
        step(Toolbox(tmp_path / "ws"), 80, settle=False)
        log = EventLog.from_jsonl(directory(tmp_path) / "events.jsonl")
        assert [e.seq for e in log] == list(range(len(log)))
        fold = status(Toolbox(tmp_path / "ws"))
        state = reduce(log)
        assert fold["n_queries"] == state.n_queries == 80
        assert fold["n_events"] == len(log)
        assert fold["budget"]["spent"]["seconds"] == state.spent.seconds
        assert reduce(resume(directory(tmp_path)).events) == state

    def test_replay_of_reservations_made_before_the_snapshot(self, tmp_path):
        """Pending work opened long ago is settled by a process that never read its reservation."""
        tb = make(tmp_path, 0)
        old = step(tb, 0, settle=False)
        for i in range(1, 200):
            step(tb, i)
        r = call(Toolbox(tmp_path / "ws"), "settle_evaluation", "late", evaluation_id=old,
                 outcome="completed", value=9.0, settled_cost={"seconds": 3.0})
        assert r["ok"] and r["result"]["settled_cost"]["seconds"] == 3.0
        assert status(Toolbox(tmp_path / "ws"))["pending"] == []
        assert status(Toolbox(tmp_path / "ws"))["best"]["value"] == 9.0


class TestLookupsBeyondTheSnapshot:
    def test_a_settled_evaluation_is_reported_as_such_with_who_settled_it(self, tmp_path):
        tb = make(tmp_path, 0)
        ev = step(tb, 0, actor=HUMAN)
        for i in range(1, 150):
            step(tb, i)
        r = call(Toolbox(tmp_path / "ws"), "settle_evaluation", "again", evaluation_id=ev,
                 outcome="completed", value=1.0, settled_cost={"seconds": 1.0})
        assert r["error"]["code"] == "already_settled"
        assert r["error"]["details"]["actor"] == HUMAN
        assert r["error"]["details"]["request_id"] == "s0"
        assert r["error"]["details"]["kind"] == "oracle_result"

    def test_an_unknown_evaluation_is_unknown(self, tmp_path):
        make(tmp_path, 150)
        r = call(Toolbox(tmp_path / "ws"), "cancel_evaluation", "x", evaluation_id="ev-999999")
        assert r["error"]["code"] == "unknown_evaluation"

    def test_retries_of_old_requests_replay_from_the_journal(self, tmp_path):
        make(tmp_path, 150)
        fresh = Toolbox(tmp_path / "ws")
        again = fresh.call("reserve_evaluation", {"campaign_id": "c", "actor": AGENT,
                                                  "request_id": "r3", "params": {"x": 0.03}})
        assert again["replayed"] and not again.get("reconstructed")
        conflict = fresh.call("reserve_evaluation", {"campaign_id": "c", "actor": AGENT,
                                                     "request_id": "r3", "params": {"x": 0.5}})
        assert conflict["error"]["code"] == "idempotency_conflict"

    def test_old_requests_are_reconstructed_from_the_record_if_the_journal_is_gone(self, tmp_path):
        make(tmp_path, 150)
        (directory(tmp_path) / "requests.jsonl").unlink()
        again = Toolbox(tmp_path / "ws").call("settle_evaluation", {
            "campaign_id": "c", "actor": AGENT, "request_id": "s3", "evaluation_id": "ev-000011",
            "outcome": "completed", "value": 3.0, "settled_cost": {"seconds": 1.0}})
        assert again["ok"] and again["reconstructed"] and again["result"]["evaluation_id"] == "ev-000011"

    def test_get_events_pages_cover_the_whole_log_from_a_new_process(self, tmp_path):
        make(tmp_path, 120)
        tb = Toolbox(tmp_path / "ws")
        call(tb, "stop_campaign", "stop")  # the session is now a light, writable one
        seen, since = [], 0
        while since is not None:
            page = tb.call("get_events", {"campaign_id": "c", "since_seq": since, "limit": 100})
            assert page["ok"]
            seen += page["result"]["events"]
            since = page["result"]["next_seq"]
        on_disk = EventLog.from_jsonl(directory(tmp_path) / "events.jsonl")
        assert [e["seq"] for e in seen] == list(range(len(on_disk)))
        assert page["result"]["total_events"] == len(on_disk)


class TestRequestIndex:
    def test_index_spans_point_at_the_events_of_each_request(self, tmp_path):
        make(tmp_path, 100)
        index = json.loads((directory(tmp_path) / "events.index.json").read_text())
        data = (directory(tmp_path) / "events.jsonl").read_bytes()
        assert index["spans"]
        for rid, (start, end) in index["spans"].items():
            lines = [json.loads(line) for line in data[start:end].splitlines()]
            assert lines and all(line["request_id"] == rid for line in lines)

    def test_index_is_refreshed_by_a_new_process_that_finds_it_stale(self, tmp_path):
        make(tmp_path, 0)
        stale = json.loads((directory(tmp_path) / "events.index.json").read_text())["n_events"]
        for i in range(3 * INDEX_EVERY // 3 + 3):
            step(Toolbox(tmp_path / "ws"), i)
        fresh = json.loads((directory(tmp_path) / "events.index.json").read_text())["n_events"]
        total = len(EventLog.from_jsonl(directory(tmp_path) / "events.jsonl"))
        assert fresh > stale and total - fresh <= INDEX_EVERY + 3

    def test_a_long_lived_process_does_not_rewrite_the_index_on_every_commit(self, tmp_path):
        tb = make(tmp_path, 0)
        index = directory(tmp_path) / "events.index.json"
        writes = []
        real = store_mod.write_atomic

        def counting(path, *a, **k):
            if str(path).endswith("events.index.json"):
                writes.append(path)
            return real(path, *a, **k)

        store_mod.write_atomic, saved = counting, real
        try:
            for i in range(200):
                step(tb, i)
        finally:
            store_mod.write_atomic = saved
        assert len(writes) <= 1 and index.exists()


def _damage_snapshot(f):
    def damage(tmp_path):
        path = directory(tmp_path) / "snapshot.json"
        path.write_text(f(path.read_text()))
    return damage


def _damage_index(f):
    def damage(tmp_path):
        path = directory(tmp_path) / "events.index.json"
        path.write_text(f(path.read_text()))
    return damage


def _edit(key, value, where="snapshot"):
    def apply(text):
        doc = json.loads(text)
        doc[key] = value
        return json.dumps(doc)
    return (_damage_snapshot if where == "snapshot" else _damage_index)(apply)


def _lying_fold(tmp_path):  # content changed, checksum not updated
    path = directory(tmp_path) / "snapshot.json"
    doc = json.loads(path.read_text())
    doc["fold"]["n_queries"] = 999
    path.write_text(json.dumps(doc))


def _forget(name):
    return lambda tmp_path: (directory(tmp_path) / name).unlink()


def _edit_last_event(tmp_path):
    path = directory(tmp_path) / "events.jsonl"
    lines = path.read_text().splitlines()
    last = json.loads(lines[-1])
    last["timestamp"] += 1.0
    lines[-1] = json.dumps(last)
    path.write_text("\n".join(lines) + "\n")


def _swap_index_from_another_history(tmp_path):
    other = tmp_path / "other"
    make(other, 25)
    for i in range(25, 60):
        step(Toolbox(other / "ws"), i)
    shutil.copy(directory(other) / "events.index.json", directory(tmp_path) / "events.index.json")


DAMAGE = {
    "snapshot_garbage": _damage_snapshot(lambda t: "not json"),
    "snapshot_truncated": _damage_snapshot(lambda t: t[:40]),
    "snapshot_schema": _edit("schema", "cadaques.snapshot/9"),
    "snapshot_offset": _edit("offset", 10 ** 9),
    "snapshot_digest": _edit("last_line_sha256", "0" * 64),
    "snapshot_spec": _edit("spec_sha256", "0" * 64),
    "snapshot_count": _edit("n_events", 3),
    "snapshot_fold_lies": _lying_fold,
    "snapshot_missing": _forget("snapshot.json"),
    "index_garbage": _damage_index(lambda t: "not json"),
    "index_truncated": _damage_index(lambda t: t[:40]),
    "index_schema": _edit("schema", "cadaques.index/9", "index"),
    "index_offset": _edit("offset", 10 ** 9, "index"),
    "index_digest": _edit("last_line_sha256", "0" * 64, "index"),
    "index_missing": _forget("events.index.json"),
    "index_of_another_history": _swap_index_from_another_history,
    "last_event_edited": _edit_last_event,
}


class TestFallbackToReplay:
    @pytest.mark.parametrize("damage", sorted(DAMAGE))
    def test_any_mismatch_replays_the_log_and_gives_the_right_answers(self, tmp_path, monkeypatch, damage):
        make(tmp_path, 70)
        reference = tmp_path / "reference"
        shutil.copytree(tmp_path / "ws", reference / "ws")
        DAMAGE[damage](tmp_path)
        spy = Spy(monkeypatch)
        tb = Toolbox(tmp_path / "ws")
        ev = step(tb, 500, settle=False)
        assert spy.cold == 1, "the damaged cache must be proved wrong and replaced by a replay"
        assert call(tb, "settle_evaluation", "s500", evaluation_id=ev, outcome="completed",
                    value=1.0, settled_cost={"seconds": 2.0})["ok"]
        # the same two calls on an untouched copy give the same record
        ref = Toolbox(reference / "ws")
        step(ref, 500, settle=False)
        call(ref, "settle_evaluation", "s500", evaluation_id=ev, outcome="completed",
             value=1.0, settled_cost={"seconds": 2.0})
        if damage == "last_event_edited":
            return  # the log itself differs from the reference by design
        assert normalized(directory(tmp_path) / "events.jsonl") == normalized(
            reference / "ws" / "c" / "events.jsonl")
        assert status(Toolbox(tmp_path / "ws")) == status(Toolbox(reference / "ws"))

    def test_the_next_commit_after_a_fallback_repairs_the_caches(self, tmp_path, monkeypatch):
        make(tmp_path, 70)
        _forget("events.index.json")(tmp_path)
        _damage_snapshot(lambda t: "garbage")(tmp_path)
        step(Toolbox(tmp_path / "ws"), 500)
        spy = Spy(monkeypatch)
        step(Toolbox(tmp_path / "ws"), 501)
        assert spy.cold == 0 and spy.light == 1

    def test_snapshot_lagging_the_log_is_folded_forward_not_replayed(self, tmp_path, monkeypatch):
        make(tmp_path, 30)
        stale = {n: (directory(tmp_path) / n).read_text() for n in ("snapshot.json", "events.index.json")}
        for i in range(30, 36):
            step(Toolbox(tmp_path / "ws"), i)
        for name, text in stale.items():  # both caches now lag the log by 18 events
            (directory(tmp_path) / name).write_text(text)
        spy = Spy(monkeypatch)
        tb = Toolbox(tmp_path / "ws")
        assert status(tb)["n_queries"] == 36
        step(tb, 40)
        assert spy.cold == 0
        assert status(Toolbox(tmp_path / "ws"))["n_queries"] == 37

    def test_a_torn_append_after_the_snapshot_is_ignored_and_truncated(self, tmp_path, monkeypatch):
        make(tmp_path, 30)
        with (directory(tmp_path) / "events.jsonl").open("ab") as fh:
            fh.write(b'{"schema": "cadaques.events/2", "seq": 999, "kind": "stop')
        spy = Spy(monkeypatch)
        step(Toolbox(tmp_path / "ws"), 99)
        assert spy.cold == 0
        events, _, _ = read_events_from(directory(tmp_path) / "events.jsonl")
        assert [e.seq for e in events] == list(range(len(events)))

    def test_drivers_that_learn_from_history_still_replay(self, tmp_path, monkeypatch):
        tb = Toolbox(tmp_path / "ws")
        assert call(tb, "create_campaign", "create", spec=spec("random"))["ok"]
        for i in range(10):
            r = call(tb, "reserve_evaluation", f"r{i}")
            call(tb, "settle_evaluation", f"s{i}", evaluation_id=r["result"]["evaluation_id"],
                 outcome="completed", value=1.0, settled_cost={"seconds": 1.0})
        spy = Spy(monkeypatch)
        assert call(Toolbox(tmp_path / "ws"), "reserve_evaluation", "next")["ok"]
        assert spy.cold == 1 and spy.light == 0

    def test_a_snapshot_never_reaches_a_view_of_another_campaign(self, tmp_path):
        """The spec digest ties a snapshot to its campaign's declaration."""
        make(tmp_path, 20)
        other = tmp_path / "other"
        make(other, 25)
        shutil.copy(directory(other) / "snapshot.json", directory(tmp_path) / "snapshot.json")
        shutil.copy(directory(other) / "events.index.json", directory(tmp_path) / "events.index.json")
        s = status(Toolbox(tmp_path / "ws"))
        assert s["n_queries"] == 20
