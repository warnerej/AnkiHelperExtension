from concurrent.futures import Future
from datetime import date

from addon import group_state as gs
from addon.errors import AuthError, NetworkError
from addon.group_loader import (
    UNEXPECTED_ERROR,
    GroupLoader,
    GroupsSnapshot,
    load_snapshot,
)
from addon.models import GroupReviews, GroupSummary

G1 = GroupSummary("g1", "One", "owner", 2)
G2 = GroupSummary("g2", "Two", "member", 3)


def reviews(group_id: str) -> GroupReviews:
    return GroupReviews(group_id, date(2026, 1, 1), date(2026, 1, 31))


def snap(selected: str = "g1", groups=None) -> GroupsSnapshot:
    return GroupsSnapshot(groups or [G1, G2], selected, reviews(selected))


class Harness:
    def __init__(self, active: bool = True, remembered: str | None = None) -> None:
        self.now = 0.0
        self.jobs: list = []  # (task, done) queued by run_in_background
        self.timers: list = []  # (ms, fn)
        self.changes = 0
        self.notes: list[str] = []
        self.warnings: list[str] = []
        self.saved: list[str] = []
        self.fetched: list[str | None] = []
        self.config_reloads = 0
        self.active = active
        self.remembered = remembered
        self.loader = GroupLoader(
            fetch=self._fetch,
            run_in_background=lambda task, done: self.jobs.append((task, done)),
            single_shot=lambda ms, fn: self.timers.append((ms, fn)),
            reload_config=self._reload,
            get_selected=lambda: self.remembered,
            save_selected=self.saved.append,
            on_change=self._changed,
            is_active=lambda: self.active,
            notify=self.notes.append,
            warn=self.warnings.append,
            clock=lambda: self.now,
        )

    def _fetch(self, wanted: str | None) -> GroupsSnapshot:
        self.fetched.append(wanted)
        return snap(wanted or "g1")

    def _reload(self) -> None:
        self.config_reloads += 1

    def _changed(self) -> None:
        self.changes += 1

    def finish(self, result=None, exc=None) -> None:
        _task, done = self.jobs.pop(0)
        future: Future = Future()
        if exc is not None:
            future.set_exception(exc)
        else:
            future.set_result(result if result is not None else GroupsSnapshot())
        done(future)


def test_load_snapshot_picks_wanted_else_first_else_nothing():
    got: list[str] = []

    def get(gid: str) -> GroupReviews:
        got.append(gid)
        return reviews(gid)

    assert load_snapshot(lambda: [G1, G2], get, "g2").selected == "g2"
    assert load_snapshot(lambda: [G1, G2], get, "gone").selected == "g1"
    assert load_snapshot(lambda: [G1, G2], get, None).selected == "g1"
    assert got == ["g2", "g1", "g1"]
    empty = load_snapshot(lambda: [], get, "g1")
    assert empty == GroupsSnapshot() and len(got) == 3


def test_ensure_fetches_once_then_loads_remembered_group():
    h = Harness(remembered="g2")
    h.loader.ensure()
    h.loader.ensure()  # in flight: ignored
    assert len(h.jobs) == 1
    assert h.loader.status == gs.LOADING
    assert h.config_reloads == 1
    _task, _done = h.jobs[0]
    h.finish(_task())
    assert h.fetched == ["g2"]
    assert h.loader.status == gs.LOADED
    assert h.loader.selected == "g2"
    assert h.loader.groups == [G1, G2]
    assert h.loader.reviews is not None and h.loader.reviews.group_id == "g2"
    assert h.changes == 1
    h.loader.ensure()  # fresh: nothing to do
    assert not h.jobs


def test_mark_stale_forces_refresh_on_next_ensure():
    h = Harness()
    h.loader.ensure()
    h.finish(snap())
    h.loader.ensure()
    assert not h.jobs
    h.loader.mark_stale()
    h.loader.ensure()
    assert len(h.jobs) == 1
    assert h.loader.status == gs.LOADED  # old data stays up while refreshing


def test_unknown_remembered_group_falls_back_and_is_saved():
    h = Harness(remembered="gone")
    h.loader.ensure()
    h.finish(snap("g1"))
    assert h.loader.selected == "g1"
    assert h.saved == ["g1"]


def test_no_groups_loads_empty():
    h = Harness()
    h.loader.ensure()
    h.finish(GroupsSnapshot())
    assert h.loader.status == gs.LOADED
    assert h.loader.groups == [] and h.loader.selected is None
    assert h.saved == []


def test_select_saves_clears_and_fetches_the_new_group():
    h = Harness(remembered="g1")
    h.loader.ensure()
    h.finish(snap("g1"))
    h.loader.select("g2")
    assert h.saved == ["g2"]
    assert h.loader.reviews is None and h.loader.status == gs.LOADING
    task, _ = h.jobs[0]
    task()
    assert h.fetched[-1] == "g2"
    h.finish(snap("g2"))
    assert h.loader.reviews is not None and h.loader.reviews.group_id == "g2"


def test_select_ignores_current_and_unknown_groups():
    h = Harness(remembered="g1")
    h.loader.ensure()
    h.finish(snap("g1"))
    h.loader.select("g1")
    h.loader.select("nope")
    assert not h.jobs and not h.saved


def test_select_while_loading_restarts_with_new_group():
    h = Harness()
    h.loader.ensure()
    h.finish(snap("g1"))
    h.now = gs.REFRESH_SECONDS
    h.loader.ensure()  # refresh of g1 in flight
    h.loader.select("g2")
    assert len(h.jobs) == 1  # no second request yet
    h.finish(snap("g1"))  # the stale result arrives
    assert h.loader.reviews is None  # not shown under g2
    assert len(h.jobs) == 1  # restarted
    h.jobs[0][0]()
    assert h.fetched[-1] == "g2"
    h.finish(snap("g2"))
    assert h.loader.reviews is not None and h.loader.reviews.group_id == "g2"


def test_stale_data_refreshes_and_keeps_old_data_on_failure():
    h = Harness()
    h.loader.ensure()
    h.finish(snap())
    h.now = gs.REFRESH_SECONDS
    h.loader.ensure()
    assert h.loader.status == gs.LOADED  # still shown while refreshing
    h.finish(exc=NetworkError("down"))
    assert h.loader.status == gs.LOADED
    assert h.loader.reviews is not None
    assert not h.timers


def test_server_down_retries_while_groups_tab_open():
    h = Harness()
    h.loader.ensure()
    h.finish(exc=NetworkError("down"))
    assert h.loader.status == gs.LOADING
    assert h.loader.attempts == 1
    assert [ms for ms, _ in h.timers] == [gs.RETRY_SECONDS * 1000]
    h.timers.pop()[1]()
    assert len(h.jobs) == 1  # retried


def test_retry_timer_does_nothing_when_tab_closed():
    h = Harness()
    h.loader.ensure()
    h.finish(exc=NetworkError("down"))
    h.active = False
    h.timers.pop()[1]()
    assert not h.jobs


def test_auth_error_stops_and_retry_command_restarts():
    h = Harness()
    h.loader.ensure()
    h.finish(exc=AuthError("bad"))
    assert h.loader.status == gs.ERROR
    assert not h.timers
    h.loader.retry_after_error()
    assert h.loader.status == gs.LOADING
    assert len(h.jobs) == 1


def test_retry_after_error_ignored_unless_error():
    h = Harness()
    h.loader.retry_after_error()
    assert not h.jobs


def test_manual_refresh_reports_result_and_errors():
    h = Harness()
    h.loader.refresh_manually()
    h.finish(snap())
    assert h.notes == ["AnkiHelper: loaded 2 groups"]
    h.loader.refresh_manually()
    h.finish(exc=NetworkError("down"))
    assert h.warnings == ["down"]
    assert h.loader.status == gs.ERROR
    assert h.loader.reviews is not None  # earlier data kept


def test_manual_failure_does_not_retry():
    h = Harness()
    h.loader.refresh_manually()
    h.finish(exc=NetworkError("down"))
    assert h.loader.status == gs.ERROR
    assert not h.timers


def test_unexpected_exception_is_reported():
    h = Harness()
    h.loader.refresh_manually()
    h.finish(exc=RuntimeError("boom"))
    assert h.warnings == [UNEXPECTED_ERROR]
    assert h.loader.status == gs.ERROR


def test_reset_clears_state_and_ignores_a_fetch_in_flight():
    h = Harness(remembered="g1")
    h.loader.ensure()
    task, _done = h.jobs[0]
    h.finish(task())
    h.loader.mark_stale()
    h.loader.ensure()  # second fetch now in flight
    changes = h.changes
    h.loader.reset()
    assert h.loader.groups == [] and h.loader.selected is None
    assert h.loader.reviews is None and h.loader.status == gs.IDLE
    assert h.saved[-1] == ""
    assert h.changes == changes + 1
    h.finish(snap())  # late result from before the reset
    assert h.loader.groups == [] and h.loader.status == gs.IDLE
    assert h.changes == changes + 1
    h.loader.ensure()  # starts a fresh fetch
    assert len(h.jobs) == 1
