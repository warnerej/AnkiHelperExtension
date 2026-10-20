from concurrent.futures import Future

from addon import friends_state as fs
from addon.errors import AuthError, NetworkError
from addon.friends_loader import UNEXPECTED_ERROR, FriendsLoader


class Harness:
    def __init__(self, active: bool = True) -> None:
        self.now = 0.0
        self.jobs: list = []  # (task, done) queued by run_in_background
        self.timers: list = []  # (ms, fn)
        self.changes = 0
        self.notes: list[str] = []
        self.warnings: list[str] = []
        self.config_reloads = 0
        self.active = active
        self.loader = FriendsLoader(
            fetch=dict,
            run_in_background=lambda task, done: self.jobs.append((task, done)),
            single_shot=lambda ms, fn: self.timers.append((ms, fn)),
            reload_config=self._reload,
            on_change=self._changed,
            is_active=lambda: self.active,
            notify=self.notes.append,
            warn=self.warnings.append,
            clock=lambda: self.now,
        )

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
            future.set_result(result if result is not None else {})
        done(future)


def test_ensure_fetches_once_then_loads():
    h = Harness()
    h.loader.ensure()
    h.loader.ensure()  # in flight: ignored
    assert len(h.jobs) == 1
    assert h.loader.status == fs.LOADING
    assert h.config_reloads == 1
    h.finish({"bob": None})
    assert h.loader.status == fs.LOADED
    assert h.loader.reviews == {"bob": None}
    assert h.changes == 1
    h.loader.ensure()  # fresh: nothing to do
    assert not h.jobs


def test_stale_data_refreshes_and_keeps_old_data_on_failure():
    h = Harness()
    h.loader.ensure()
    h.finish({"bob": None})
    h.now = fs.REFRESH_SECONDS
    h.loader.ensure()
    assert h.loader.status == fs.LOADED  # still shown while refreshing
    h.finish(exc=NetworkError("down"))
    assert h.loader.status == fs.LOADED
    assert h.loader.reviews == {"bob": None}
    assert not h.timers


def test_server_down_retries_while_friends_tab_open():
    h = Harness()
    h.loader.ensure()
    h.finish(exc=NetworkError("down"))
    assert h.loader.status == fs.LOADING
    assert h.loader.attempts == 1
    assert [ms for ms, _ in h.timers] == [fs.RETRY_SECONDS * 1000]
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
    assert h.loader.status == fs.ERROR
    assert not h.timers
    h.loader.retry_after_error()
    assert h.loader.status == fs.LOADING
    assert len(h.jobs) == 1


def test_retry_after_error_ignored_unless_error():
    h = Harness()
    h.loader.retry_after_error()
    assert not h.jobs


def test_manual_refresh_reports_result_and_errors():
    h = Harness()
    h.loader.refresh_manually()
    h.finish({"a": None, "b": object()})
    assert h.notes == ["AnkiHelper: loaded 1 of 2 friends"]
    h.loader.refresh_manually()
    h.finish(exc=NetworkError("down"))
    assert h.warnings == ["down"]
    assert h.loader.status == fs.ERROR
    assert h.loader.reviews  # earlier data kept


def test_manual_failure_does_not_retry():
    h = Harness()
    h.loader.refresh_manually()
    h.finish(exc=NetworkError("down"))
    assert h.loader.status == fs.ERROR
    assert not h.timers


def test_unexpected_exception_is_reported():
    h = Harness()
    h.loader.refresh_manually()
    h.finish(exc=RuntimeError("boom"))
    assert h.warnings == [UNEXPECTED_ERROR]
    assert h.loader.status == fs.ERROR
