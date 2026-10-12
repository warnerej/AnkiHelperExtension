from concurrent.futures import Future
from pathlib import Path
from typing import Any

from aqt import gui_hooks, mw
from aqt.qt import QAction
from aqt.utils import showWarning, tooltip

from .api_client import ApiClient
from .errors import AnkiHelperError
from .log import get_logger, setup_logging
from .models import UserResponse
from .settings import Settings

log = get_logger()


class Controller:
    """Main-thread owner of config and the API client.

    Workers never touch `mw`: they read `self._config`, a snapshot taken here.
    """

    def __init__(self) -> None:
        assert mw is not None
        self._addon = mw.addonManager.addonFromModule(__name__)
        self._config: dict[str, Any] = {}
        self.client = ApiClient(self._settings)
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
        mw.taskman.run_in_background(self.client.close)

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
    mw.addonManager.setConfigUpdatedAction(__name__, controller.on_config_updated)
    gui_hooks.profile_will_close.append(controller.on_profile_close)
    return controller
