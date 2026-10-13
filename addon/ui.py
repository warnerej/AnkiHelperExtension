import time
from concurrent.futures import Future
from datetime import date
from pathlib import Path
from typing import Any

from aqt import gui_hooks, mw
from aqt.qt import QAction
from aqt.utils import showWarning, tooltip

from .api_client import ApiClient
from .errors import AnkiHelperError
from .log import get_logger, setup_logging
from .models import DailyReview, UserResponse
from .settings import Settings
from .stats import today_review_count

log = get_logger()

PUSH_THROTTLE_SECONDS = 300


class Controller:
    """Main-thread owner of config and the API client.

    Workers never touch `mw`: they read `self._config`, a snapshot taken here.
    """

    def __init__(self) -> None:
        assert mw is not None
        self._addon = mw.addonManager.addonFromModule(__name__)
        self._config: dict[str, Any] = {}
        self.client = ApiClient(self._settings)
        self._last_pushed: tuple[date, int] | None = None
        self._last_push_time = 0.0
        self.reload_config()

    def _settings(self) -> Settings:
        return Settings.from_config(self._config)

    def reload_config(self) -> None:
        assert mw is not None
        self._config = mw.addonManager.getConfig(self._addon) or {}
        setup_logging(
            Path(__file__).parent / "user_files",
            bool(self._config.get("debug_logging")),
        )

    def on_config_updated(self, _new_config: Any) -> None:
        # Credentials or URL may have changed: drop the old session, then reload.
        assert mw is not None
        mw.taskman.run_in_background(self.client.logout)
        self.reload_config()

    def on_profile_close(self) -> None:
        assert mw is not None
        # Push the final total, then close, in one worker so they cannot race.
        reading = self._read_today()

        def work() -> None:
            if reading is not None:
                try:
                    self.client.put_daily_reviews(*reading)
                except AnkiHelperError as exc:
                    log.info("final review push failed: %s", exc)
            self.client.close()

        mw.taskman.run_in_background(work)

    def _read_today(self) -> tuple[date, int] | None:
        assert mw is not None
        if mw.col is None:
            return None
        return today_review_count(mw.col)

    def push_today(self, *, manual: bool = False) -> None:
        """Send today's running review total. Must be called on the main thread."""
        assert mw is not None
        reading = self._read_today()
        if reading is None:
            return
        if reading == self._last_pushed:
            if manual:
                tooltip("AnkiHelper: reviews already up to date")
            return
        self._last_push_time = time.monotonic()
        study_date, total = reading
        mw.taskman.run_in_background(
            lambda: self.client.put_daily_reviews(study_date, total),
            lambda future: self._on_push_result(future, reading, manual),
        )

    def on_card_answered(self, *_args: Any) -> None:
        if time.monotonic() - self._last_push_time >= PUSH_THROTTLE_SECONDS:
            self.push_today()

    def on_sync_finished(self) -> None:
        self.push_today()

    def _on_push_result(
        self,
        future: Future[DailyReview],
        reading: tuple[date, int],
        manual: bool,
    ) -> None:
        try:
            saved = future.result()
        except AnkiHelperError as exc:
            log.info("review push failed: %s", exc)
            if manual:
                showWarning(str(exc), title="AnkiHelper")
            return
        except Exception:
            log.exception("unexpected error pushing reviews")
            if manual:
                showWarning(
                    "Unexpected error. See user_files/ankihelper.log.",
                    title="AnkiHelper",
                )
            return
        self._last_pushed = reading
        log.info("pushed %d reviews for %s", saved.total_reviews, saved.study_date)
        if manual:
            tooltip(
                f"AnkiHelper: saved {saved.total_reviews} reviews for {saved.study_date}"
            )

    def test_connection(self) -> None:
        assert mw is not None
        self.reload_config()
        tooltip("AnkiHelper: connecting...")
        mw.taskman.run_in_background(self.client.get_me, self._on_connection_result)

    def _on_connection_result(self, future: Future[UserResponse]) -> None:
        try:
            me = future.result()
        except AnkiHelperError as exc:
            log.info("test connection failed: %s", exc)
            showWarning(str(exc), title="AnkiHelper")
            return
        except Exception:
            log.exception("unexpected error during test connection")
            showWarning(
                "Unexpected error. See user_files/ankihelper.log.", title="AnkiHelper"
            )
            return
        tooltip(f"AnkiHelper: connected as {me.email or 'unknown user'}")


def setup() -> Controller:
    assert mw is not None
    controller = Controller()
    action = QAction("AnkiHelper: Test connection", mw)
    action.triggered.connect(controller.test_connection)
    mw.form.menuTools.addAction(action)
    push_action = QAction("AnkiHelper: Push today's reviews", mw)
    push_action.triggered.connect(lambda: controller.push_today(manual=True))
    mw.form.menuTools.addAction(push_action)
    mw.addonManager.setConfigUpdatedAction(__name__, controller.on_config_updated)
    gui_hooks.profile_will_close.append(controller.on_profile_close)
    gui_hooks.sync_did_finish.append(controller.on_sync_finished)
    gui_hooks.reviewer_did_answer_card.append(controller.on_card_answered)
    return controller
