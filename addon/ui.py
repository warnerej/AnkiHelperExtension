import time
from concurrent.futures import Future
from pathlib import Path
from typing import Any

from aqt import gui_hooks, mw
from aqt.qt import (
    QAction,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QVBoxLayout,
)
from aqt.utils import askUser, showInfo, showWarning, tooltip

from .api_client import ApiClient
from .errors import AnkiHelperError, NotLoggedInError
from .friends_loader import FriendsLoader
from .heatmap import RETRY_CMD, VIEW_CMD, render_heatmap_html
from .log import get_logger, setup_logging
from .models import DailyReview, SignupResponse, UserResponse
from .settings import USERNAME_RE, Settings, strip_legacy_credentials
from .stats import TodayStats, today_stats, year_stats
from .token_store import TokenStore

log = get_logger()

PUSH_THROTTLE_SECONDS = 300


class CredentialsDialog(QDialog):
    """Email + password, plus a username when signing up. Validates before accepting."""

    def __init__(self, parent: Any, title: str, *, signup: bool) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self._signup = signup
        self.email = QLineEdit()
        self.username = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form = QFormLayout()
        form.addRow("Email", self.email)
        if signup:
            form.addRow("Username", self.username)
        form.addRow("Password", self.password)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        if signup:
            layout.addWidget(
                _hint("Username: 3-20 letters, digits or _. Password: 8+ characters.")
            )
        layout.addWidget(buttons)

    def _validate(self) -> None:
        problem = None
        if not self.email.text().strip() or not self.password.text():
            problem = "Enter your email and password."
        elif self._signup and not USERNAME_RE.fullmatch(
            self.username.text().strip().lower()
        ):
            problem = "Username must be 3-20 letters, digits or _."
        elif self._signup and len(self.password.text()) < 8:
            problem = "Password must be at least 8 characters."
        if problem:
            showWarning(problem, title="AnkiHelper", parent=self)
            return
        self.accept()


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    return label


def _unexpected(what: str) -> None:
    log.exception("unexpected error during %s", what)
    showWarning("Unexpected error. See user_files/ankihelper.log.", title="AnkiHelper")


class Controller:
    """Main-thread owner of config and the API client.

    Workers never touch `mw`: they read `self._config`, a snapshot taken here.
    """

    def __init__(self) -> None:
        assert mw is not None
        self._addon = mw.addonManager.addonFromModule(__name__)
        self._config: dict[str, Any] = {}
        user_files = Path(__file__).parent / "user_files"
        self.client = ApiClient(self._settings, TokenStore(user_files / "session.json"))
        self._last_pushed: TodayStats | None = None
        self._last_push_time = 0.0
        self.view = "mine"
        self.friends = FriendsLoader(
            fetch=self.client.get_friends_reviews,
            run_in_background=lambda task, done: mw.taskman.run_in_background(
                task, done
            ),
            single_shot=lambda ms, fn: mw.progress.single_shot(ms, fn, False),
            reload_config=self.reload_config,
            on_change=self._refresh_deck_browser,
            is_active=lambda: self.view == "friends",
            notify=tooltip,
            warn=lambda message: showWarning(message, title="AnkiHelper"),
        )
        self.reload_config()
        self._migrate_legacy_config()

    def _settings(self) -> Settings:
        return Settings.from_config(self._config)

    def reload_config(self) -> None:
        assert mw is not None
        self._config = mw.addonManager.getConfig(self._addon) or {}
        setup_logging(
            Path(__file__).parent / "user_files",
            bool(self._config.get("debug_logging")),
        )

    def _migrate_legacy_config(self) -> None:
        """Older versions kept email/password in config.json: drop them silently."""
        assert mw is not None
        cleaned, had_password = strip_legacy_credentials(self._config)
        if cleaned == self._config:
            return
        mw.addonManager.writeConfig(self._addon, cleaned)
        self._config = cleaned
        if had_password:
            log.info("removed legacy email/password from config; log in via the menu")

    def on_config_updated(self, _new_config: Any) -> None:
        # A changed base_url rebuilds the client; the stored token is tied to its URL.
        self.reload_config()

    def restore_session(self) -> None:
        """At startup, resume the stored session; prompt for login if it was rejected."""
        assert mw is not None
        mw.taskman.run_in_background(self.client.restore, self._on_restore_result)

    def _on_restore_result(self, future: Future[None]) -> None:
        try:
            future.result()
        except NotLoggedInError as exc:
            log.info("no session restored: %s", exc)
            if "expired" in str(exc) and askUser(
                f"{exc}\n\nLog in now?", title="AnkiHelper"
            ):
                self.log_in()
        except AnkiHelperError as exc:
            log.info("session restore failed: %s", exc)  # e.g. offline; retried on use
        except Exception:
            log.exception("unexpected error restoring session")

    def on_profile_close(self) -> None:
        assert mw is not None
        # Push the final total, then close, in one worker so they cannot race.
        reading = self._read_today()

        def work() -> None:
            if reading is not None:
                try:
                    self.client.put_daily_reviews(reading)
                except AnkiHelperError as exc:
                    log.info("final review push failed: %s", exc)
            self.client.close()

        mw.taskman.run_in_background(work)

    def _read_today(self) -> TodayStats | None:
        assert mw is not None
        if mw.col is None:
            return None
        return today_stats(mw.col)

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
        mw.taskman.run_in_background(
            lambda: self.client.put_daily_reviews(reading),
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
        reading: TodayStats,
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
            retention = (
                f"{reading.retention:g}% retention"
                if reading.retention is not None
                else "no retention yet"
            )
            tooltip(
                f"AnkiHelper: saved {saved.total_reviews} reviews, {retention}, "
                f"{reading.study_minutes} min for {saved.study_date}"
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

    def on_deck_browser_render(self, _deck_browser: Any, content: Any) -> None:
        """Append the heatmap to the main screen."""
        assert mw is not None
        mine = year_stats(mw.col) if mw.col is not None else None
        content.stats += render_heatmap_html(
            mine, self.friends.reviews, self.view, self.friends.status
        )

    def on_js_message(
        self, handled: tuple[bool, Any], message: str, _context: Any
    ) -> tuple[bool, Any]:
        if message == RETRY_CMD:
            self.friends.retry_after_error()
            return (True, None)
        if not message.startswith(VIEW_CMD):
            return handled
        view = message.removeprefix(VIEW_CMD)
        if view in ("mine", "friends"):
            self.view = view
            if view == "friends":
                self.friends.ensure()
            self._refresh_deck_browser()
        return (True, None)

    def _refresh_deck_browser(self) -> None:
        assert mw is not None
        if mw.state == "deckBrowser":
            mw.deckBrowser.refresh()

    def log_in(self) -> None:
        assert mw is not None
        self.reload_config()
        dialog = CredentialsDialog(mw, "AnkiHelper: Log in", signup=False)
        if not dialog.exec():
            return
        email, password = dialog.email.text().strip(), dialog.password.text()
        tooltip("AnkiHelper: logging in...")
        mw.taskman.run_in_background(
            lambda: self.client.login(email, password), self._on_login_result
        )

    def _on_login_result(self, future: Future[None]) -> None:
        try:
            future.result()
        except AnkiHelperError as exc:
            log.info("login failed: %s", exc)
            showWarning(str(exc), title="AnkiHelper")
            return
        except Exception:
            _unexpected("login")
            return
        tooltip("AnkiHelper: logged in")
        self.push_today()

    def sign_up(self) -> None:
        assert mw is not None
        self.reload_config()
        dialog = CredentialsDialog(mw, "AnkiHelper: Sign up", signup=True)
        if not dialog.exec():
            return
        email, password = dialog.email.text().strip(), dialog.password.text()
        username = dialog.username.text().strip().lower()
        mw.taskman.run_in_background(
            lambda: self.client.signup(email, password, username),
            self._on_signup_result,
        )

    def _on_signup_result(self, future: Future[SignupResponse]) -> None:
        try:
            result = future.result()
        except AnkiHelperError as exc:
            log.info("signup failed: %s", exc)
            showWarning(str(exc), title="AnkiHelper")
            return
        except Exception:
            _unexpected("signup")
            return
        if result.confirmation_required:
            showInfo(
                "Account created. Confirm your email, then use Tools > AnkiHelper > Log in.",
                title="AnkiHelper",
            )
        else:
            tooltip("AnkiHelper: account created and logged in")
            self.push_today()

    def log_out(self) -> None:
        assert mw is not None
        mw.taskman.run_in_background(
            self.client.logout, lambda _future: tooltip("AnkiHelper: logged out")
        )


def setup() -> Controller:
    assert mw is not None
    controller = Controller()
    menu = QMenu("AnkiHelper", mw)
    for item in (
        ("Log in...", controller.log_in),
        ("Sign up...", controller.sign_up),
        ("Log out", controller.log_out),
        None,
        ("Test connection", controller.test_connection),
        ("Push today's reviews", lambda: controller.push_today(manual=True)),
        ("Refresh friends", controller.friends.refresh_manually),
    ):
        if item is None:
            menu.addSeparator()
            continue
        label, handler = item
        action = QAction(label, mw)
        action.triggered.connect(handler)
        menu.addAction(action)
    mw.form.menuTools.addMenu(menu)
    mw.addonManager.setConfigUpdatedAction(__name__, controller.on_config_updated)
    gui_hooks.profile_will_close.append(controller.on_profile_close)
    gui_hooks.sync_did_finish.append(controller.on_sync_finished)
    gui_hooks.webview_did_receive_js_message.append(controller.on_js_message)
    gui_hooks.deck_browser_will_render_content.append(controller.on_deck_browser_render)
    gui_hooks.reviewer_did_answer_card.append(controller.on_card_answered)
    controller.restore_session()
    return controller
