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

## Remaining, by priority

### Medium
- **Finish item 6.** The account flows (login, signup, logout, delete, `_reset_account_state`, `_last_pushed`, `_logged_in`) are still in `Controller`. Suggested first step: a small pure `AccountState` class holding those fields and the reset logic, unit-tested without Qt, then move the flows behind it. `_on_restore_result` still has its own try/except by design.
- **Item 9, test gaps.**
  - `ui.py` (about 590 lines) and `groups_dialog.py` have no tests. Extract decisions into pure modules (as `group_loader.py` and `history_logic.py` do), or add a thin test with a fake `mw`.
  - Split `tests/test_api_client.py` (about 880 lines): it also holds `Settings` and `strip_friends` tests. Add `test_settings.py`; `test_session.py` now exists.
  - The roadmap smoke tests (`addon-roadmap.md` around lines 158-164) are still unchecked.

### Low
- **10. Docs drift.**
  - Stale test count in `README.md` (now 253).
  - Duplicated phrase near `README.md:20`.
  - `reset_logic.py` and `dialogs.py` missing from the README layout tree.
  - `pyproject.toml` description still says "friends heatmaps".
  - `addon-roadmap.md` says Tasks 1-3 are uncommitted and Task 5 is "Next"; both are out of date.
  - Handoff docs in this folder are stale but README lists them as live.
  - `API_GUIDE.md` mentions "Kantanly" without explanation.
  - `refresh-logout-followups.md` item 2 (rate limits) is a backend task.
- **11. Dead code from the removed friends feature.** `heatmap.render_members_html`, the "(user not found)" branch in `_render_profile`, the `.ah-friends`/`.ah-friend` CSS names, `HeatmapCell.lit`, `groups_logic.is_owner_role`, unused `DetailControls` fields (only `leave` and `invite_code` are read), `Session.is_logged_in`, `docs/images/heatmap-friends.png`. Only tests use several of these; remove the tests with them.
- **12. Inconsistent error mapping in `api_client.py`.** `put_daily_reviews` and `get_me` hand-roll try/except instead of `_parse`; `delete_group`, `allow_username` and similar discard the response unchecked.
- **13. `groups_dialog.py`.**
  - `_call` silently drops clicks while `_busy`.
  - `_fill_list` can trigger two loads.
  - `return self._invalid(...)` returns a None expression.
  - The `finished` callback can touch a closed dialog; check `alive()` from `dialogs.py`.
  - Owner detection uses `invite_code is not None` (`groups_logic.py` around lines 67-77); prefer an explicit role from the server.
- **14. Typing.** `Any` callbacks (`_read_year` in `ui.py`), untyped `**kwargs` (`Session._post`), `col: Any` in `stats.py`, untyped fixtures in `conftest.py`. Pyright is in basic mode; try `typeCheckingMode = "standard"`.
- **15. Minor security/logging.**
  - `user_files/` is created with default permissions and the directory isn't fsynced after `os.replace` (`token_store.py`).
  - The log redaction filter ignores `stack_info` and mutates `record.msg` (`log.py`).
  - `Settings` accepts a `base_url` with userinfo.
  - Some messages repeat the "AnkiHelper:" prefix.
- **16. Scattered constants** (`PUSH_THROTTLE_SECONDS`/`YEAR_CACHE_SECONDS` in `ui.py`, `group_state.py`, `history_backfill.py`, `api_client.py`). Optional `constants.py`.

## Suggested order
Live-Anki check of the finished work, then the `AccountState` extraction and item 9 together (it makes the flows testable), then 11 and 10 (cheap, shrink the surface), then 12-16 as time allows.
