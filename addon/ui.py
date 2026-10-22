import time
from collections.abc import Callable
from concurrent.futures import Future
from pathlib import Path
from typing import Any

from aqt import gui_hooks, mw
from aqt.operations import QueryOp
from aqt.qt import (
    QAction,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    Qt,
    QVBoxLayout,
)
from aqt.utils import askUser, showInfo, showWarning, tooltip

from .api_client import ApiClient
from .delete_logic import DELETE_WARNING, validate_delete
from .errors import AnkiHelperError, NotLoggedInError
from .group_loader import GroupLoader, load_snapshot
from .groups_dialog import GroupsDialog
from .heatmap import GROUP_CMD, GROUPS_CMD, RETRY_CMD, VIEW_CMD, render_heatmap_html
from .history_backfill import HistoryBackfiller
from .history_logic import (
    CONFIRM_WORD,
    has_reviews,
    manual_confirmation,
    result_message,
    today_row,
    typed_confirmation_ok,
)
from .log import get_logger, setup_logging
from .models import DailyReview, HistoryResult, SignupResponse, UserResponse, YearReviews
from .reset_logic import RESET_SENT_MESSAGE, validate_reset_email
from .settings import USERNAME_RE, Settings, strip_friends, strip_legacy_credentials
from .stats import YEAR_DAYS, TodayStats, today_stats, year_stats
from .token_store import TokenStore

log = get_logger()

PUSH_THROTTLE_SECONDS = 300


class CredentialsDialog(QDialog):
    """Email + password, plus a username when signing up. Validates before accepting."""

    def __init__(
        self,
        parent: Any,
        title: str,
        *,
        signup: bool,
        on_forgot: Callable[[str, Any], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self._signup = signup
        self._forgot: QPushButton | None = None
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
        if on_forgot is not None and not signup:
            self._forgot = QPushButton("Forgot password?")
            self._forgot.setFlat(True)
            self._forgot.setAutoDefault(False)
            self._forgot.setCursor(Qt.CursorShape.PointingHandCursor)
            self._forgot.clicked.connect(
                lambda: on_forgot(self.email.text().strip(), self)
            )
            layout.addWidget(self._forgot, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(buttons)

    def set_forgot_enabled(self, enabled: bool) -> None:
        if self._forgot is not None:
            self._forgot.setEnabled(enabled)

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


class DeleteAccountDialog(QDialog):
    """Account deletion confirmation: type the account email and the password."""

    def __init__(self, parent: Any, expected_email: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("AnkiHelper: Delete account")
        self._expected_email = expected_email
        self.email = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form = QFormLayout()
        form.addRow(f"Type {expected_email} to confirm", self.email)
        form.addRow("Password", self.password)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            ok.setText("Delete account")
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(_hint(DELETE_WARNING))
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _validate(self) -> None:
        problem = validate_delete(
            self._expected_email, self.email.text(), self.password.text()
        )
        if problem:
            showWarning(problem, title="AnkiHelper", parent=self)
            return
        self.accept()


class EmptyUploadDialog(QDialog):
    """Stronger confirm for uploading an empty year: type the word to proceed."""

    def __init__(self, parent: Any, message: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("AnkiHelper: Re-upload review history")
        self.typed = QLineEdit()
        form = QFormLayout()
        form.addRow(f"Type {CONFIRM_WORD} to confirm", self.typed)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(_hint(message))
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _validate(self) -> None:
        if not typed_confirmation_ok(self.typed.text()):
            showWarning(
                f"Type {CONFIRM_WORD} to confirm, or cancel.",
                title="AnkiHelper",
                parent=self,
            )
            return
        self.accept()


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    return label


def _alive(widget: Any) -> bool:
    """False once Qt has deleted the widget's C++ object (e.g. the dialog was closed)."""
    try:
        widget.isVisible()
    except RuntimeError:
        return False
    return True


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
        self._forgot_pending = False
        self._last_push_time = 0.0
        self.view = "mine"
        self._groups_dialog: GroupsDialog | None = None
        self.groups = GroupLoader(
            fetch=lambda wanted: load_snapshot(
                self.client.list_groups, self.client.get_group_reviews, wanted
            ),
            run_in_background=lambda task, done: mw.taskman.run_in_background(
                task, done
            ),
            single_shot=lambda ms, fn: mw.progress.single_shot(ms, fn, False),
            reload_config=self.reload_config,
            get_selected=lambda: str(self._config.get("selected_group") or "") or None,
            save_selected=self._save_selected_group,
            on_change=self._refresh_deck_browser,
            is_active=lambda: self.view == "groups" and not self._closing,
            notify=tooltip,
            warn=lambda message: showWarning(message, title="AnkiHelper"),
        )
        self._logged_in = False
        self._closing = False
        self._synced_this_session = False
        self._reupload_inflight = False
        self.backfill = HistoryBackfiller(
            is_logged_in=lambda: self._logged_in,
            uses_sync=lambda: mw.pm.sync_auth() is not None,
            synced_this_session=lambda: self._synced_this_session,
            fetch_status=self.client.get_history_status,
            read_year=self._read_year,
            upload=self.client.upload_history,
            run_in_background=lambda task, done: mw.taskman.run_in_background(
                task, done
            ),
            notify=tooltip,
            mark_groups_stale=self.groups.mark_stale,
            set_last_pushed=self._set_last_pushed,
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

    def _read_year(self, days: int, success: Any, failure: Any) -> None:
        """Read the last `days` days' stats through QueryOp, which owns the collection."""
        assert mw is not None
        QueryOp(
            parent=mw, op=lambda col: year_stats(col, days), success=success
        ).failure(failure).run_in_background()

    def reupload_history(self) -> None:
        """Menu action: replace the server's last year with this collection's data."""
        if not self._logged_in:
            showInfo(str(NotLoggedInError()), title="AnkiHelper")
            return
        if self._reupload_inflight:
            return
        self._reupload_inflight = True
        self._read_year(
            YEAR_DAYS, self._on_year_for_reupload, self._on_reupload_read_failed
        )

    def _on_reupload_read_failed(self, exc: Exception) -> None:
        self._reupload_inflight = False
        log.error("reading the collection for re-upload failed", exc_info=exc)
        showWarning(
            "Could not read your review history. See user_files/ankihelper.log.",
            title="AnkiHelper",
        )

    def _on_year_for_reupload(self, history: YearReviews) -> None:
        assert mw is not None
        message = manual_confirmation(history, self._synced_this_session)
        if has_reviews(history):
            confirmed = askUser(message, title="AnkiHelper")
        else:
            confirmed = bool(EmptyUploadDialog(mw, message).exec())
        if not confirmed:
            self._reupload_inflight = False
            return
        tooltip("AnkiHelper: uploading review history...")
        mw.taskman.run_in_background(
            lambda: self.client.upload_history(history),
            lambda future: self._on_reupload_result(future, history),
        )

    def _on_reupload_result(
        self, future: Future[HistoryResult], history: YearReviews
    ) -> None:
        self._reupload_inflight = False
        try:
            result = future.result()
        except AnkiHelperError as exc:
            log.info("history re-upload failed: %s", exc)
            showWarning(str(exc), title="AnkiHelper")
            return
        except Exception:
            _unexpected("history re-upload")
            return
        log.info("history re-upload wrote %d days", result.days_written)
        tooltip(result_message(result))
        self.groups.mark_stale()
        if self.view == "groups":
            self.groups.ensure()
        row = today_row(history)
        if row is not None:
            self._set_last_pushed(row)

    def _set_last_pushed(self, stats: TodayStats) -> None:
        self._last_pushed = stats

    def _save_selected_group(self, group_id: str) -> None:
        assert mw is not None
        self._config = {**self._config, "selected_group": group_id}
        mw.addonManager.writeConfig(self._addon, self._config)

    def _migrate_legacy_config(self) -> None:
        """Drop retired config keys: email/password silently, `friends` with a note."""
        assert mw is not None
        cleaned, had_password = strip_legacy_credentials(self._config)
        cleaned, had_friends = strip_friends(cleaned)
        if cleaned == self._config:
            return
        mw.addonManager.writeConfig(self._addon, cleaned)
        self._config = cleaned
        if had_password:
            log.info("removed legacy email/password from config; log in via the menu")
        if had_friends:
            log.info("removed retired friends list from config")
            mw.progress.single_shot(3000, self._show_friends_removed_note, False)

    def _show_friends_removed_note(self) -> None:
        showInfo(
            "AnkiHelper: the `friends` setting is no longer used and has been "
            "removed. Sharing now works through groups: create or join one from "
            "Tools > AnkiHelper > Groups.",
            title="AnkiHelper",
        )

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
            self._logged_in = False
            log.info("no session restored: %s", exc)
            if "expired" in str(exc) and askUser(
                f"{exc}\n\nLog in now?", title="AnkiHelper"
            ):
                self.log_in()
        except AnkiHelperError as exc:
            log.info("session restore failed: %s", exc)  # e.g. offline; retried on use
            self._logged_in = True  # a stored session exists; backfill retries after sync
        except Exception:
            log.exception("unexpected error restoring session")
        else:
            self._logged_in = True
            self.backfill.maybe_run()

    def open_groups(self) -> None:
        """Show the (single, non-modal) Groups dialog."""
        assert mw is not None
        if self._groups_dialog is None:
            dialog = GroupsDialog(
                mw,
                self.client,
                lambda task, done: mw.taskman.run_in_background(task, done),
            )
            dialog.finished.connect(lambda _result: self._on_groups_closed())
            self._groups_dialog = dialog
            dialog.show()
            dialog.reload()
        else:
            self._groups_dialog.show()
            self._groups_dialog.raise_()
            self._groups_dialog.activateWindow()

    def _on_groups_closed(self) -> None:
        self._groups_dialog = None
        self.groups.mark_stale()  # the user may have created, joined or left groups
        if self.view == "groups":
            self.groups.ensure()

    def on_profile_open(self) -> None:
        """A profile was reopened in the same Anki run (first open: setup() restored)."""
        if not self._closing:
            return
        self._closing = False
        self._synced_this_session = False
        self.client.reopen()
        self.restore_session()

    def on_profile_close(self) -> None:
        assert mw is not None
        # Anki syncs after this hook; from here on nothing may start network work
        # (a token refresh cut off by exit would lose the rotated refresh token).
        self._closing = True
        if self._groups_dialog is not None:
            self._groups_dialog.close()
        # Push the final total, then close, in one worker so they cannot race.
        reading = self._read_today()

        def work() -> None:
            if reading is not None and not self.client.has_fresh_token():
                log.info("final review push skipped: token would need a refresh")
            elif reading is not None:
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
        if self._closing:
            return
        if time.monotonic() - self._last_push_time >= PUSH_THROTTLE_SECONDS:
            self.push_today()

    def on_sync_finished(self) -> None:
        if self._closing:
            return
        self._synced_this_session = True
        self.push_today()
        self.backfill.maybe_run(after_sync=True)

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
            mine,
            self.groups.groups,
            self.groups.selected,
            self.groups.reviews,
            self.view,
            self.groups.status,
        )

    def on_js_message(
        self, handled: tuple[bool, Any], message: str, _context: Any
    ) -> tuple[bool, Any]:
        if message == RETRY_CMD:
            self.groups.retry_after_error()
            return (True, None)
        if message == GROUPS_CMD:
            self.open_groups()
            return (True, None)
        if message.startswith(GROUP_CMD):
            self.groups.select(message.removeprefix(GROUP_CMD))
            return (True, None)
        if not message.startswith(VIEW_CMD):
            return handled
        view = message.removeprefix(VIEW_CMD)
        if view in ("mine", "groups"):
            self.view = view
            if view == "groups":
                self.groups.ensure()
            self._refresh_deck_browser()
        return (True, None)

    def _refresh_deck_browser(self) -> None:
        assert mw is not None
        if mw.state == "deckBrowser":
            mw.deckBrowser.refresh()

    def log_in(self) -> None:
        assert mw is not None
        self.reload_config()
        dialog = CredentialsDialog(
            mw, "AnkiHelper: Log in", signup=False, on_forgot=self.forgot_password
        )
        if not dialog.exec():
            return
        email, password = dialog.email.text().strip(), dialog.password.text()
        tooltip("AnkiHelper: logging in...")
        mw.taskman.run_in_background(
            lambda: self.client.login(email, password), self._on_login_result
        )

    def forgot_password(self, prefill_email: str, parent: Any) -> None:
        if self._forgot_pending:
            return
        email, ok = QInputDialog.getText(
            parent,
            "AnkiHelper: Reset password",
            "Email for the account to reset:",
            text=prefill_email,
        )
        if not ok:
            return
        email = email.strip()
        problem = validate_reset_email(email)
        if problem:
            showWarning(problem, title="AnkiHelper", parent=parent)
            return
        assert mw is not None
        self._forgot_pending = True
        parent.set_forgot_enabled(False)
        mw.taskman.run_in_background(
            lambda: self.client.forgot_password(email),
            lambda future: self._on_forgot_result(future, parent),
        )

    def _on_forgot_result(self, future: Future[None], parent: Any) -> None:
        self._forgot_pending = False
        if _alive(parent):
            parent.set_forgot_enabled(True)
        else:
            parent = mw
        try:
            future.result()
        except AnkiHelperError as exc:
            log.info("password reset request failed: %s", exc)
            showWarning(str(exc), title="AnkiHelper", parent=parent)
            return
        except Exception:
            _unexpected("password reset")
            return
        showInfo(RESET_SENT_MESSAGE, title="AnkiHelper", parent=parent)

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
        self._logged_in = True
        self.push_today()
        self.backfill.maybe_run()

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
            self._logged_in = True
            self.push_today()
            self.backfill.maybe_run()

    def log_out(self) -> None:
        assert mw is not None
        self._logged_in = False
        self.backfill.reset()
        mw.taskman.run_in_background(
            self.client.logout, lambda _future: tooltip("AnkiHelper: logged out")
        )

    def delete_account(self) -> None:
        assert mw is not None
        mw.taskman.run_in_background(self.client.get_me, self._on_me_for_delete)

    def _on_me_for_delete(self, future: Future[UserResponse]) -> None:
        assert mw is not None
        try:
            me = future.result()
        except AnkiHelperError as exc:
            log.info("account lookup before deletion failed: %s", exc)
            showWarning(str(exc), title="AnkiHelper")
            return
        except Exception:
            _unexpected("account lookup")
            return
        dialog = DeleteAccountDialog(mw, me.email or "")
        if not dialog.exec():
            return
        password = dialog.password.text()
        mw.taskman.run_in_background(
            lambda: self.client.delete_account(password), self._on_delete_result
        )

    def _on_delete_result(self, future: Future[None]) -> None:
        try:
            future.result()
        except AnkiHelperError as exc:
            log.info("account deletion failed: %s", exc)
            showWarning(str(exc), title="AnkiHelper")
            return
        except Exception:
            _unexpected("account deletion")
            return
        if self._groups_dialog is not None:
            self._groups_dialog.close()
        self._last_pushed = None
        self._logged_in = False
        self.backfill.reset()
        self.groups.reset()
        tooltip("AnkiHelper: account deleted")


def setup() -> Controller:
    assert mw is not None
    controller = Controller()
    menu = QMenu("AnkiHelper", mw)
    for item in (
        ("Log in...", controller.log_in),
        ("Sign up...", controller.sign_up),
        ("Log out", controller.log_out),
        ("Re-upload review history...", controller.reupload_history),
        ("Delete account...", controller.delete_account),
        ("Groups...", controller.open_groups),
        None,
        ("Test connection", controller.test_connection),
        ("Push today's reviews", lambda: controller.push_today(manual=True)),
        ("Refresh groups", controller.groups.refresh_manually),
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
    gui_hooks.profile_did_open.append(controller.on_profile_open)
    gui_hooks.profile_will_close.append(controller.on_profile_close)
    gui_hooks.sync_did_finish.append(controller.on_sync_finished)
    gui_hooks.webview_did_receive_js_message.append(controller.on_js_message)
    gui_hooks.deck_browser_will_render_content.append(controller.on_deck_browser_render)
    gui_hooks.reviewer_did_answer_card.append(controller.on_card_answered)
    controller.restore_session()
    return controller
