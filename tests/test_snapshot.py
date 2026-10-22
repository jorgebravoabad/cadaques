"""Derived-state snapshots and session caching (ADR-0015): one test class per guarantee.

The event log stays the source of truth; every cached or snapshotted form of
the state is validated against it and folded forward, never trusted blindly.
"""

from __future__ import annotations

import json
import shutil

import pytest

from cadaques import EventLog, replay
from cadaques.agent import Toolbox
from cadaques.core.spec import CampaignSpec
from cadaques.runtime.replay import resume
from cadaques.runtime.state import StateFold, reduce

AGENT = {"kind": "agent", "id": "snap-agent", "model": "test-model", "version": "1"}
SPEC = {
    "oracle": {"class": "cadaques.oracles.external.ExternalOracle",
               "params": {"tariff": {"seconds": 1.0}}},
    "driver": {"class": "cadaques.drivers.reference.RandomDriver",
               "params": {"space": {"x": [0.0, 1.0]}}},
    "budget_total": {"seconds": 1e6},
    "task": {"bounds": {"x": [0.0, 1.0]}, "direction": "maximize", "fidelities": {},
             "success_value": None, "name": "t"},
    "seed": 5,
}


def call(tb, tool, **args):
    if tool not in ("get_campaign_status", "get_events", "list_campaigns"):
        args = {"actor": AGENT, **args}
    args.setdefault("campaign_id", "c")
    return tb.call(tool, args)


def step(tb, i, *, settle=True, rid=""):
    r = call(tb, "reserve_evaluation", request_id=f"{rid}r{i}")
    assert r["ok"], r
    ev = r["result"]
    if settle:
        s = call(tb, "settle_evaluation", request_id=f"{rid}s{i}", evaluation_id=ev["evaluation_id"],
                 outcome="completed", value=-(ev["params"]["x"] - 0.3) ** 2,
                 settled_cost={"seconds": 1.0})
        assert s["ok"], s
    return ev


def make(tmp_path, n=0):
    tb = Toolbox(tmp_path / "ws")
    assert call(tb, "create_campaign", request_id="create", spec=SPEC)["ok"]
    for i in range(n):
        step(tb, i)
    return tb


def status(tb):
    s = call(tb, "get_campaign_status")
    assert s["ok"], s
    s = s["result"]
    # ages are wall-clock; everything else must be reproducible
    for p in s["pending"]:
        p.pop("age_seconds")
    return s


def log_path(tmp_path):
    return tmp_path / "ws" / "c" / "events.jsonl"


def snap_path(tmp_path):
    return tmp_path / "ws" / "c" / "snapshot.json"


def full_state(tmp_path):
    return reduce(EventLog.from_jsonl(log_path(tmp_path)))


class TestSnapshotEquivalence:
    def test_incremental_fold_equals_full_reduce_at_every_prefix(self, tmp_path):
        make(tmp_path, 15)
        events = EventLog.from_jsonl(log_path(tmp_path)).events
        fold = StateFold()
        for k, event in enumerate(events, 1):
            fold.feed(event)
            assert fold.state() == reduce(events[:k])

    def test_fold_survives_a_json_roundtrip_mid_log(self, tmp_path):
        make(tmp_path, 12)
        events = EventLog.from_jsonl(log_path(tmp_path)).events
        fold = StateFold()
        for event in events[:11]:
            fold.feed(event)
        revived = StateFold.from_dict(json.loads(json.dumps(fold.to_dict())))
        assert revived.state() == fold.state()
        for event in events[11:]:
            revived.feed(event)
        assert revived.state() == reduce(events)

    def test_written_snapshot_describes_the_log(self, tmp_path):
        make(tmp_path, 10)
        doc = json.loads(snap_path(tmp_path).read_text())
        assert doc["schema"] == "cadaques.snapshot/1"
        assert doc["n_events"] == len(EventLog.from_jsonl(log_path(tmp_path)))
        assert doc["offset"] == log_path(tmp_path).stat().st_size
        assert StateFold.from_dict(doc["fold"]).state() == full_state(tmp_path)

    def test_snapshot_view_equals_full_load_status(self, tmp_path):
        make(tmp_path, 10)
        step(Toolbox(tmp_path / "ws"), 99, settle=False)  # leave one reservation pending
        via_snapshot = status(Toolbox(tmp_path / "ws"))  # cold process: snapshot view
        snap_path(tmp_path).unlink()
        via_log = status(Toolbox(tmp_path / "ws"))  # no snapshot: full replay
        assert via_snapshot == via_log
        assert len(via_log["pending"]) == 1

    def test_snapshot_is_folded_forward_over_newer_events(self, tmp_path):
        make(tmp_path, 6)
        stale = snap_path(tmp_path).read_text()
        tb = Toolbox(tmp_path / "ws")
        for i in range(6, 10):
            step(tb, i)
        snap_path(tmp_path).write_text(stale)  # snapshot now lags the log by 8 events
        assert status(Toolbox(tmp_path / "ws"))["n_events"] == len(
            EventLog.from_jsonl(log_path(tmp_path)))
        assert status(Toolbox(tmp_path / "ws"))["n_queries"] == 10

    def test_live_state_equals_replay_of_the_record(self, tmp_path):
        tb = make(tmp_path, 25)
        s = status(tb)
        spec = CampaignSpec.load(tmp_path / "ws" / "c" / "spec.json")
        state = replay(spec, log_path(tmp_path))
        assert state == full_state(tmp_path)
        assert s["n_queries"] == state.n_queries == 25
        assert s["best"]["value"] == state.best_value
        assert s["budget"]["spent"]["seconds"] == state.spent.seconds
        # and the campaign a fresh process resumes agrees with the cached one
        assert reduce(resume(tmp_path / "ws" / "c").events) == state

    def test_list_campaigns_uses_views_and_agrees(self, tmp_path):
        make(tmp_path, 4)
        item = Toolbox(tmp_path / "ws").call("list_campaigns")["result"]["campaigns"][0]
        state = full_state(tmp_path)
        assert item["readable"] and item["n_queries"] == state.n_queries == 4
        assert item["best_value"] == state.best_value


class TestSnapshotIsNeverTrustedBlindly:
    @pytest.mark.parametrize("damage", [
        lambda t: t.write_text("not json at all"),
        lambda t: t.write_text("{}"),
        lambda t: t.write_text(json.dumps({**json.loads(t.read_text()), "schema": "cadaques.snapshot/9"})),
        lambda t: t.write_text(json.dumps({**json.loads(t.read_text()), "last_line_sha256": "0" * 64})),
        lambda t: t.write_text(json.dumps({**json.loads(t.read_text()), "offset": 10 ** 9})),
        lambda t: t.write_text(json.dumps({**json.loads(t.read_text()), "spec_sha256": "0" * 64})),
        lambda t: t.write_text(json.dumps({**json.loads(t.read_text()), "n_events": 3})),
        lambda t: t.write_text(t.read_text()[:40]),
        lambda t: t.unlink(),
    ], ids=["garbage", "empty", "schema", "digest", "offset", "spec", "count", "truncated", "missing"])
    def test_damaged_snapshot_falls_back_to_the_log(self, tmp_path, damage):
        make(tmp_path, 7)
        damage(snap_path(tmp_path))
        s = status(Toolbox(tmp_path / "ws"))
        state = full_state(tmp_path)
        assert s["n_queries"] == state.n_queries == 7
        assert s["best"]["value"] == state.best_value

    def test_snapshot_whose_state_lies_is_caught_by_the_digest(self, tmp_path):
        """A snapshot from a different history of the same length must not be used."""
        make(tmp_path, 5)
        doc = json.loads(snap_path(tmp_path).read_text())
        other = tmp_path / "other"
        shutil.copytree(tmp_path / "ws", other / "ws")
        tb2 = Toolbox(other / "ws")
        # rewrite the other workspace's last event so its bytes differ from the snapshot's digest
        p = other / "ws" / "c" / "events.jsonl"
        lines = p.read_text().splitlines()
        last = json.loads(lines[-1])
        last["payload"]["value"] = 123.0
        lines[-1] = json.dumps(last)
        p.write_text("\n".join(lines) + "\n")
        (other / "ws" / "c" / "snapshot.json").write_text(json.dumps(doc))
        assert status(tb2)["best"]["value"] == 123.0  # the log wins

    def test_a_snapshot_never_overrides_the_log_for_mutations(self, tmp_path):
        tb = make(tmp_path, 5)
        doc = json.loads(snap_path(tmp_path).read_text())
        doc["fold"]["n_queries"] = 999
        snap_path(tmp_path).write_text(json.dumps(doc))
        tb2 = Toolbox(tmp_path / "ws")  # cold process, mutating call
        step(tb2, 5)
        assert status(tb2)["n_queries"] == 6 == full_state(tmp_path).n_queries
        assert status(Toolbox(tmp_path / "ws"))["n_queries"] == 6
        del tb


class TestSessionCache:
    def test_cache_is_revalidated_when_the_log_is_rewritten_underneath(self, tmp_path):
        tb = make(tmp_path, 5)
        status(tb)
        p = log_path(tmp_path)
        lines = p.read_text().splitlines()
        last = json.loads(lines[-1])
        last["payload"]["value"] = 77.0
        lines[-1] = json.dumps(last)
        p.write_text("\n".join(lines) + "\n")
        assert call(tb, "get_events", since_seq=len(lines) - 1)["result"]["events"][0]["payload"]["value"] == 77.0
        step(tb, 5)
        assert status(tb)["best"]["value"] == 77.0

    def test_cache_survives_read_only_and_failed_calls(self, tmp_path):
        tb = make(tmp_path, 3)
        store = tb.store
        status(tb)
        assert "c" in store._cache
        ev = step(tb, 3, settle=False)
        bad = call(tb, "settle_evaluation", request_id="bad", evaluation_id="ev-999999",
                   outcome="completed", value=1.0, settled_cost={"seconds": 1.0})
        assert bad["error"]["code"] == "unknown_evaluation"
        assert "c" in store._cache  # nothing uncommitted: the session is reused
        ok = call(tb, "settle_evaluation", request_id="good", evaluation_id=ev["evaluation_id"],
                  outcome="completed", value=0.5, settled_cost={"seconds": 1.0})
        assert ok["ok"]
        dup = call(tb, "settle_evaluation", request_id="dup", evaluation_id=ev["evaluation_id"],
                   outcome="completed", value=0.5, settled_cost={"seconds": 1.0})
        assert dup["error"]["code"] == "already_settled"
        assert status(tb)["n_queries"] == 4

    def test_rejected_points_are_committed_and_the_session_stays_consistent(self, tmp_path):
        tb = Toolbox(tmp_path / "ws")
        spec = {**SPEC, "driver": {"class": "cadaques.drivers.external.ExternalDriver", "params": {}}}
        assert call(tb, "create_campaign", request_id="create", spec=spec)["ok"]
        r = call(tb, "reserve_evaluation", request_id="oob", params={"x": 7.0})
        assert r["error"]["code"] == "invalid_query"
        assert status(tb)["n_rejected"] == 1 == full_state(tmp_path).n_rejected
        assert status(Toolbox(tmp_path / "ws"))["n_rejected"] == 1

    def test_idempotent_replay_and_conflict_still_work_cold(self, tmp_path):
        tb = make(tmp_path, 2)
        first = call(tb, "reserve_evaluation", request_id="same")
        again = Toolbox(tmp_path / "ws").call(
            "reserve_evaluation", {"campaign_id": "c", "actor": AGENT, "request_id": "same"})
        assert again["replayed"] and again["result"] == first["result"]
        bad = Toolbox(tmp_path / "ws").call(
            "reserve_evaluation", {"campaign_id": "c", "actor": AGENT, "request_id": "same",
                                   "tag": "different"})
        assert bad["error"]["code"] == "idempotency_conflict"

    def test_crash_window_reconstruction_uses_the_request_index(self, tmp_path):
        tb = make(tmp_path, 3)
        first = call(tb, "reserve_evaluation", request_id="lost")
        journal = tmp_path / "ws" / "c" / "requests.jsonl"
        kept = [ln for ln in journal.read_text().splitlines() if '"request_id": "lost"' not in ln]
        journal.write_text("\n".join(kept) + "\n")  # committed, response never journaled
        again = Toolbox(tmp_path / "ws").call(
            "reserve_evaluation", {"campaign_id": "c", "actor": AGENT, "request_id": "lost"})
        assert again["reconstructed"] and again["result"]["evaluation_id"] == first["result"]["evaluation_id"]


class TestAppendOnlyCommit:
    def test_a_torn_final_line_is_not_a_committed_event(self, tmp_path):
        tb = make(tmp_path, 4)
        n = len(EventLog.from_jsonl(log_path(tmp_path)))
        with log_path(tmp_path).open("ab") as fh:
            fh.write(b'{"schema": "cadaques.events/2", "seq": %d, "kind": "ora' % n)
        assert len(EventLog.from_jsonl(log_path(tmp_path))) == n
        assert status(Toolbox(tmp_path / "ws"))["n_events"] == n
        step(tb, 4)  # the next writer truncates the fragment and carries on
        events = EventLog.from_jsonl(log_path(tmp_path))
        assert len(events) == n + 3 and [e.seq for e in events] == list(range(n + 3))
        assert b"ora\n" not in log_path(tmp_path).read_bytes()

    def test_a_corrupt_complete_line_is_an_error_not_silently_dropped(self, tmp_path):
        make(tmp_path, 2)
        lines = log_path(tmp_path).read_text().splitlines()
        lines[2] = lines[2][:20]
        log_path(tmp_path).write_text("\n".join(lines) + "\n")
        with pytest.raises(ValueError):
            EventLog.from_jsonl(log_path(tmp_path))

    def test_events_are_appended_not_rewritten(self, tmp_path):
        tb = make(tmp_path, 3)
        before = log_path(tmp_path).read_bytes()
        step(tb, 3)
        after = log_path(tmp_path).read_bytes()
        assert after.startswith(before) and len(after) > len(before)

    def test_file_log_round_trips_through_replay_after_many_calls(self, tmp_path):
        make(tmp_path, 40)
        events = EventLog.from_jsonl(log_path(tmp_path))
        assert [e.seq for e in events] == list(range(len(events)))
        assert all(e.request_id for e in events)


class TestIndependentInstances:
    def test_two_toolboxes_interleaving_stay_consistent(self, tmp_path):
        a = make(tmp_path)
        b = Toolbox(tmp_path / "ws")
        reserved = []
        for i in range(10):
            ta, tbx = (a, b) if i % 2 == 0 else (b, a)
            reserved.append(step(ta, i, settle=False, rid="x")["evaluation_id"])
            ev = reserved[-1]
            call(tbx, "settle_evaluation", request_id=f"set{i}", evaluation_id=ev,
                 outcome="completed", value=float(i), settled_cost={"seconds": 1.0})
        assert len(set(reserved)) == 10
        sa, sb = status(a), status(b)
        assert sa == sb
        state = full_state(tmp_path)
        assert sa["n_queries"] == state.n_queries == 10
        assert sa["budget"]["spent"]["seconds"] == state.spent.seconds
        assert state.settled_oracle.seconds == 10.0
        assert sa["best"]["value"] == state.best_value == 9.0
        assert sa["pending"] == []

    def test_interleaved_reservations_on_both_sides_see_each_other(self, tmp_path):
        a = make(tmp_path)
        b = Toolbox(tmp_path / "ws")
        e1 = step(a, 1, settle=False, rid="a")
        e2 = step(b, 2, settle=False, rid="b")
        assert e1["evaluation_id"] != e2["evaluation_id"]
        assert {p["evaluation_id"] for p in status(a)["pending"]} == {
            e1["evaluation_id"], e2["evaluation_id"]}
        # b settles a's reservation, a settles b's
        assert call(b, "settle_evaluation", request_id="sb", evaluation_id=e1["evaluation_id"],
                    outcome="completed", value=1.0, settled_cost={"seconds": 1.0})["ok"]
        assert call(a, "settle_evaluation", request_id="sa", evaluation_id=e2["evaluation_id"],
                    outcome="completed", value=2.0, settled_cost={"seconds": 1.0})["ok"]
        assert call(a, "settle_evaluation", request_id="again", evaluation_id=e1["evaluation_id"],
                    outcome="completed", value=1.0, settled_cost={"seconds": 1.0})["error"]["code"] == "already_settled"
        assert status(b)["pending"] == [] and status(b)["n_queries"] == 2

    def test_proposals_continue_the_rng_stream_across_instances(self, tmp_path):
        """A driver proposal made after another process's calls equals the single-process one."""
        solo = Toolbox(tmp_path / "solo")
        assert call(solo, "create_campaign", request_id="create", spec=SPEC)["ok"]
        xs_solo = [step(solo, i)["params"]["x"] for i in range(8)]

        a = make(tmp_path)
        b = Toolbox(tmp_path / "ws")
        xs = [step(a if i % 2 else b, i)["params"]["x"] for i in range(8)]
        assert xs == xs_solo

    def test_cached_and_cold_boxes_make_identical_campaigns(self, tmp_path):
        warm = make(tmp_path, 12)
        cold = Toolbox(tmp_path / "ws")
        assert status(warm) == status(cold)
        assert call(warm, "get_events", limit=1000)["result"] == call(cold, "get_events", limit=1000)["result"]
        # the next proposal is the same whichever box makes it (rng restored from checkpoint)
        solo = Toolbox(tmp_path / "solo")
        assert call(solo, "create_campaign", request_id="create", spec=SPEC)["ok"]
        for i in range(12):
            step(solo, i)
        assert step(cold, 12)["params"]["x"] == step(solo, 12)["params"]["x"]


class TestGetEventsPagination:
    def test_pages_cover_the_log_exactly_once(self, tmp_path):
        tb = make(tmp_path, 9)
        seen, since = [], 0
        while since is not None:
            r = call(tb, "get_events", since_seq=since, limit=4)["result"]
            seen += [e["seq"] for e in r["events"]]
            since = r["next_seq"]
        total = len(EventLog.from_jsonl(log_path(tmp_path)))
        assert seen == list(range(total))
        only = call(tb, "get_events", kinds=["oracle_result"], limit=3)["result"]
        assert [e["kind"] for e in only["events"]] == ["oracle_result"] * 3
        assert only["next_seq"] is not None and only["total_events"] == total
