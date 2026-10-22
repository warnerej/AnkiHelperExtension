import time
from concurrent.futures import Future
from pathlib import Path
from typing import Any

from aqt import gui_hooks, mw
from aqt.operations import QueryOp
from aqt.qt import QAction, QInputDialog, QMenu
from aqt.utils import askUser, showInfo, showWarning, tooltip

from .account_state import AccountState, restore_outcome
from .api_client import ApiClient
from .dialogs import CredentialsDialog, DeleteAccountDialog, EmptyUploadDialog, alive
from .errors import AnkiHelperError, NotLoggedInError
from .group_loader import GroupLoader, load_snapshot
from .groups_dialog import GroupsDialog
from .heatmap import GROUP_CMD, GROUPS_CMD, RETRY_CMD, VIEW_CMD, render_heatmap_html
from .history_backfill import HistoryBackfiller
from .history_logic import has_reviews, manual_confirmation, result_message, today_row
from .log import get_logger, setup_logging
from .models import (
    DailyReview,
    HistoryResult,
    SignupResponse,
    UserResponse,
    YearReviews,
)
from .reset_logic import RESET_SENT_MESSAGE, validate_reset_email
from .settings import Settings, strip_friends, strip_legacy_credentials
from .stats import YEAR_DAYS, TodayStats, today_stats, year_stats
from .token_store import TokenStore

log = get_logger()

PUSH_THROTTLE_SECONDS = 300
YEAR_CACHE_SECONDS = 60


def _settle[T](
    future: Future[T], what: str, *, show: bool = True, parent: Any = None
) -> tuple[bool, T | None]:
    """Unwrap a worker's future. On failure log it, warn the user (if `show`), return (False, None)."""
    try:
        return True, future.result()
    except AnkiHelperError as exc:
        log.info("%s failed: %s", what, exc)
        if show:
            showWarning(str(exc), title="AnkiHelper", parent=parent)
    except Exception:
        log.exception("unexpected error during %s", what)
        if show:
            showWarning(
                "Unexpected error. See user_files/ankihelper.log.",
                title="AnkiHelper",
                parent=parent,
            )
    return False, None


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
        self.account = AccountState(on_reset=self._forget_account_data)
        self._closing = False
        self._synced_this_session = False
        self._reupload_inflight = False
        self._forgot_pending = False
        self._last_push_time = 0.0
        self._year_cache: tuple[float, YearReviews] | None = None
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
        self.backfill = HistoryBackfiller(
            is_logged_in=lambda: self.account.logged_in,
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
            set_last_pushed=self.account.record_push,
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
        if not self.account.logged_in:
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
        ok, result = _settle(future, "history re-upload")
        if not ok or result is None:
            return
        log.info("history re-upload wrote %d days", result.days_written)
        tooltip(result_message(result))
        self.groups.mark_stale()
        if self.view == "groups":
            self.groups.ensure()
        row = today_row(history)
        if row is not None:
            self.account.record_push(row)

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
            self.account.logged_in = restore_outcome(exc) is True
            log.info("no session restored: %s", exc)
            if "expired" in str(exc) and askUser(
                f"{exc}\n\nLog in now?", title="AnkiHelper"
            ):
                self.log_in()
        except AnkiHelperError as exc:
            self.account.logged_in = restore_outcome(exc) is True
            log.info("session restore failed: %s", exc)  # offline errors retry on use
        except Exception:
            log.exception("unexpected error restoring session")
        else:
            self.account.logged_in = True
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
        if reading == self.account.last_pushed:
            if manual:
                tooltip("AnkiHelper: reviews already up to date")
            return
        self._last_push_time = time.monotonic()
        generation = self.account.generation
        mw.taskman.run_in_background(
            lambda: self.client.put_daily_reviews(reading),
            lambda future: self._on_push_result(future, reading, manual, generation),
        )

    def on_card_answered(self, *_args: Any) -> None:
        self._year_cache = None
        if self._closing:
            return
        if time.monotonic() - self._last_push_time >= PUSH_THROTTLE_SECONDS:
            self.push_today()

    def on_sync_finished(self) -> None:
        self._year_cache = None
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
        generation: int,
    ) -> None:
        ok, saved = _settle(future, "review push", show=manual)
        if not ok or saved is None:
            return
        if not self.account.record_push(reading, generation):
            log.info("discarding push result from a previous account")
            return
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
        ok, me = _settle(future, "test connection")
        if not ok or me is None:
            return
        tooltip(f"AnkiHelper: connected as {me.email or 'unknown user'}")

    def on_deck_browser_render(self, _deck_browser: Any, content: Any) -> None:
        """Append the heatmap to the main screen."""
        assert mw is not None
        mine = self._year_for_render() if self.view == "mine" else None
        content.stats += render_heatmap_html(
            mine,
            self.groups.groups,
            self.groups.selected,
            self.groups.reviews,
            self.view,
            self.groups.status,
        )

    def _year_for_render(self) -> YearReviews | None:
        """Own year of stats for the heatmap, cached; None if unavailable.

        Runs on the main thread inside a render hook, so it must be cheap on
        repeat renders and must never raise (that would break the main screen).
        """
        assert mw is not None
        if mw.col is None:
            return None
        now = time.monotonic()
        cached = self._year_cache
        if cached is not None and now - cached[0] < YEAR_CACHE_SECONDS:
            return cached[1]
        try:
            year = year_stats(mw.col)
        except Exception:
            log.exception("could not read review history for the heatmap")
            return None
        self._year_cache = (now, year)
        return year

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
        if alive(parent):
            parent.set_forgot_enabled(True)
        else:
            parent = mw
        ok, _ = _settle(future, "password reset request", parent=parent)
        if not ok:
            return
        showInfo(RESET_SENT_MESSAGE, title="AnkiHelper", parent=parent)

    def _on_login_result(self, future: Future[None]) -> None:
        ok, _ = _settle(future, "login")
        if not ok:
            return
        tooltip("AnkiHelper: logged in")
        self.account.start_session()
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
        ok, result = _settle(future, "signup")
        if not ok or result is None:
            return
        if result.confirmation_required:
            showInfo(
                "Account created. Confirm your email, then use Tools > AnkiHelper > Log in.",
                title="AnkiHelper",
            )
        else:
            tooltip("AnkiHelper: account created and logged in")
            self.account.start_session()
            self.push_today()
            self.backfill.maybe_run()

    def log_out(self) -> None:
        assert mw is not None
        if self._groups_dialog is not None:
            self._groups_dialog.close()
        self.account.reset()
        mw.taskman.run_in_background(self.client.logout, self._on_logout_result)

    def _on_logout_result(self, future: Future[None]) -> None:
        ok, _ = _settle(future, "logout")
        if ok:
            tooltip("AnkiHelper: logged out")

    def _forget_account_data(self) -> None:
        self.backfill.reset()
        self.groups.reset()

    def delete_account(self) -> None:
        assert mw is not None
        mw.taskman.run_in_background(self.client.get_me, self._on_me_for_delete)

    def _on_me_for_delete(self, future: Future[UserResponse]) -> None:
        assert mw is not None
        ok, me = _settle(future, "account lookup before deletion")
        if not ok or me is None:
            return
        dialog = DeleteAccountDialog(mw, me.email or "")
        if not dialog.exec():
            return
        password = dialog.password.text()
        mw.taskman.run_in_background(
            lambda: self.client.delete_account(password), self._on_delete_result
        )

    def _on_delete_result(self, future: Future[None]) -> None:
        ok, _ = _settle(future, "account deletion")
        if not ok:
            return
        if self._groups_dialog is not None:
            self._groups_dialog.close()
        self.account.reset()
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
