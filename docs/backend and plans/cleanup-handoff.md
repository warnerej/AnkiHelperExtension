# Cleanup handoff

Written after a repo audit. Items 1-8 are done and committed on `master`
(`27100c9`..`855142a`, nothing pushed). Items 9-16 remain. Read `CLAUDE.md` first;
its conventions still apply (no Anki imports outside the Qt modules, network off the
main thread, never log secrets, never refresh tokens during profile close).

Checks that must stay green: `uv run ruff check .`, `uv run ruff format --check .`,
`uv run pyright`, `uv run pytest -q` (253 tests), `scripts/package.sh`.

## Done (do not redo)
1. `ApiClient` thread safety: `RLock` in `_ensure_session`/`close`/`reopen`; closed-client `RuntimeError` -> `ClientClosedError` (`addon/api_client.py`).
2. Stale account state: `Controller._reset_account_state()` called on logout, delete and login/signup (`addon/ui.py`).
3. Deck browser render: year stats only in the "mine" view, 60 s cache cleared on answer/sync, never raises.
4. Session restore only treats `NetworkError`/`ServerError` as "stored session exists".
5. `session.format_detail()` turns FastAPI 422 lists into readable text (`tests/test_session.py`).
6. (Partial) Dialogs moved to `addon/dialogs.py`; nine copied try/except blocks replaced by `_settle()` in `ui.py`; flags set before callbacks in `Controller.__init__`.
7. `scripts/package.sh`: vendors from `uv.lock` into a staging dir, drops `meta.json`/`dist-info`, rejects compiled extensions, smoke-tests the zip.
8. `ruff format` applied (line length 88, `E501` ignored in lint); `.github/workflows/ci.yml` added.

## Not verified
- None of the `ui.py` changes have run in a live Anki (the Qt code has no tests). Check by hand: log out then log in as a different user (today's push must happen, groups reset); deck browser render in both views; login, signup, delete and re-upload flows still show their warnings on failure.
- The GitHub Actions workflow has never run on GitHub; only its commands were run locally.
- The new race test (`test_cold_start_threads_share_one_session_and_refresh`) passed even against the old code. The lock is correct by inspection, but the test is only a smoke check. A deterministic version would block one thread inside `_get_settings` or the transport using a `threading.Event`.

## Done in the second pass (do not redo)
- **11** Dead friends code removed (`render_members_html`, "user not found", `lit`, `is_owner_role`, unused `DetailControls` fields, `Session.is_logged_in`). CSS classes are now `.ah-group`/`.ah-member`; the screenshot is `docs/images/heatmap-groups.png`.
- **10** Docs drift fixed (README, roadmap status, `pyproject.toml`, `API_GUIDE.md`).
- **6/9** `addon/account_state.py` (`AccountState`, `restore_outcome`) owns `logged_in`, `last_pushed` and the reset; a generation counter drops a push result that arrives after the account changed. `tests/test_account_state.py`, `tests/test_settings.py` split out. The race test is now deterministic (fails without the lock).
- **12** `get_me` and `put_daily_reviews` use `_parse`. The handoff's claim that `delete_group`/`allow_username` ignore the status was wrong: `check_response` already raises; tests added.
- **13** Groups dialog: owner controls follow the role from the group list (invite-code check is only the fallback), closed-dialog guard, one load per selection, no `return self._invalid(...)`. Busy clicks are impossible because the dialog is disabled; the guard now logs.
- **14** Pyright `standard` mode on with no fallout; `Collection` type for `stats.py`, typed `_read_year`, `Session._post`, conftest fixtures.
- **15** `user_files/` is 0700, directory fsync after `os.replace`, redaction is a formatter (covers `stack_info`, leaves the record alone), `base_url` with userinfo rejected, settings messages lose the doubled "AnkiHelper:" prefix.

## Still open
- **Live-Anki check** of everything in `ui.py`, `dialogs.py` and `groups_dialog.py` (see "Not verified"), including the new Groups dialog behaviour.
- **16** Scattered constants left as they are (moving them would add import churn for little gain).
- `ui.py` is still ~590 lines. The account flows still live in `Controller`; only their state moved. Further extraction needs a fake `mw`.
- Roadmap smoke-test checkboxes need a live Anki run.
- Nothing is pushed; the CI workflow has never run on GitHub.
