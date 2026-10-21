"""Loads group reviews in the background and tracks the load state.

Free of aqt: everything Anki-specific is injected, so it can be tested in plain Python.
"""

import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field

from . import group_state as gs
from .errors import AnkiHelperError
from .log import get_logger
from .models import GroupReviews, GroupSummary

log = get_logger()

UNEXPECTED_ERROR = "Unexpected error. See user_files/ankihelper.log."


@dataclass(frozen=True)
class GroupsSnapshot:
    """The user's groups plus the reviews of the one that is selected (if any)."""

    groups: list[GroupSummary] = field(default_factory=list)
    selected: str | None = None
    reviews: GroupReviews | None = None


def load_snapshot(
    list_groups: Callable[[], list[GroupSummary]],
    get_reviews: Callable[[str], GroupReviews],
    wanted: str | None,
) -> GroupsSnapshot:
    """Fetch the group list, then the reviews of `wanted` (or the first group if
    `wanted` is unknown, e.g. left or deleted since it was remembered)."""
    groups = list_groups()
    ids = [g.id for g in groups]
    if not groups:
        return GroupsSnapshot()
    selected = wanted if wanted in ids else ids[0]
    return GroupsSnapshot(groups, selected, get_reviews(selected))


class GroupLoader:
    """Main-thread owner of the groups data and its fetch/retry state machine."""

    def __init__(
        self,
        *,
        fetch: Callable[[str | None], GroupsSnapshot],
        run_in_background: Callable[[Callable[[], GroupsSnapshot], Callable], object],
        single_shot: Callable[[int, Callable[[], None]], None],
        reload_config: Callable[[], None],
        get_selected: Callable[[], str | None],
        save_selected: Callable[[str], None],
        on_change: Callable[[], None],
        is_active: Callable[[], bool],
        notify: Callable[[str], object],
        warn: Callable[[str], object],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetch = fetch
        self._run_in_background = run_in_background
        self._single_shot = single_shot
        self._reload_config = reload_config
        self._get_selected = get_selected  # remembered group id, from config
        self._save_selected = save_selected
        self._on_change = on_change  # data or status changed: redraw
        self._is_active = is_active  # is the Groups tab showing?
        self._notify = notify
        self._warn = warn
        self._clock = clock
        self.groups: list[GroupSummary] = []
        self.selected: str | None = None
        self.reviews: GroupReviews | None = None
        self.status = gs.IDLE
        self.attempts = 0
        self._inflight = False
        self._requested: str | None = None
        self._last_fetch = 0.0
        self._generation = 0

    def ensure(self) -> None:
        """Load groups if this session has none yet (or they are stale)."""
        if self._inflight:
            return
        action = gs.action_on_open(self.status, self._clock() - self._last_fetch)
        if action is None:
            return
        if action == "fetch":
            self.status = gs.LOADING
            self.attempts = 0
        self._start(manual=False)

    def refresh_manually(self) -> None:
        if self._inflight:
            return
        self.status = gs.LOADING
        self.attempts = 0
        self._start(manual=True)
        self._on_change()

    def retry_after_error(self) -> None:
        if self.status == gs.ERROR:
            self.status = gs.IDLE
            self.ensure()
            self._on_change()

    def mark_stale(self) -> None:
        """Groups changed elsewhere (e.g. the Groups dialog): reload on next ensure()."""
        self._last_fetch = float("-inf")

    def reset(self) -> None:
        """Forget everything (e.g. the account was deleted); ignore any fetch in flight."""
        self._generation += 1
        self.groups = []
        self.selected = None
        self.reviews = None
        self.status = gs.IDLE
        self.attempts = 0
        self._inflight = False
        self._requested = None
        self._last_fetch = 0.0
        self._save_selected("")
        self._on_change()

    def select(self, group_id: str) -> None:
        """Switch to another group (from the picker) and load its reviews."""
        if group_id == self.selected or not any(g.id == group_id for g in self.groups):
            return
        self.selected = group_id
        self._save_selected(group_id)
        self.reviews = None  # never show one group's data under another's name
        self.status = gs.LOADING
        self.attempts = 0
        self._start(manual=False)  # if a fetch is in flight, its result restarts this
        self._on_change()

    def _start(self, *, manual: bool) -> None:
        if self._inflight:
            return
        self._inflight = True
        if self.status != gs.LOADED:
            self.status = gs.LOADING
        self._reload_config()
        if self.selected is None:
            self.selected = self._get_selected()
        self._requested = self.selected
        wanted = self._requested
        self._last_fetch = self._clock()
        generation = self._generation
        self._run_in_background(
            lambda: self._fetch(wanted),
            lambda future: self._on_result(future, manual, generation),
        )

    def _on_result(
        self, future: Future[GroupsSnapshot], manual: bool, generation: int
    ) -> None:
        if generation != self._generation:
            return  # reset() ran while this loaded
        self._inflight = False
        try:
            result = future.result()
        except AnkiHelperError as exc:
            log.info("groups fetch failed: %s", exc)
            self._on_failed(exc, str(exc), manual)
            return
        except Exception:
            log.exception("unexpected error fetching groups")
            self._on_failed(None, UNEXPECTED_ERROR, manual)
            return
        self.groups = result.groups
        if self._requested != self.selected:
            self._start(manual=False)  # the user switched groups while this loaded
            return
        if result.selected != self.selected:
            self.selected = result.selected
            if result.selected is not None:
                self._save_selected(result.selected)
        self.reviews = result.reviews
        self.status = gs.LOADED
        log.info("loaded %d groups", len(result.groups))
        if manual:
            self._notify(f"AnkiHelper: loaded {len(result.groups)} groups")
        self._on_change()

    def _on_failed(self, exc: Exception | None, message: str, manual: bool) -> None:
        if manual:
            self._warn(message)
        if self.status == gs.LOADED:
            return  # a background refresh failed; keep showing the old data
        self.attempts += 1
        if gs.should_retry(exc) and not manual:
            # Server down or still waking up: keep trying while Groups is open.
            self._single_shot(gs.RETRY_SECONDS * 1000, self._retry)
        else:
            self.status = gs.ERROR
        self._on_change()

    def _retry(self) -> None:
        if self._is_active() and self.status == gs.LOADING:
            self._start(manual=False)
