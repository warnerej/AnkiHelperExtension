> **Archived 2026-10-22.** Done: automatic backfill, 7-day catch-up and manual re-upload shipped (`history_backfill.py`). Smoke tests are in `ROADMAP.md`.

# AnkiHelper add-on: review history backfill

Handoff for Claude Code, for the **add-on repo** (`AnkiHelperExtension`). Work through the tasks **in order** and stop for review after each one.

**Depends on the backend:** `PUT /reviews/history` and `GET /reviews/history/status` from `backfill-backend.md` must be deployed first. If anything here disagrees with the backend's `/docs` page or its `API_GUIDE.md`, those win.

## Context

The add-on only pushes the current Anki day, so two problems follow:

- **New users show up in a group with an empty heatmap.** Their past year of reviews never leaves their computer.
- **Reviews done on a phone go missing.** They reach the desktop collection on sync, but they're usually for a past day, and past days are never pushed.

The backend now accepts a full year at once and **replaces** that date range for the user. This add-on side has two jobs:

1. **Automatic backfill:** upload the last 365 days once per account, by default, with no action needed from the user.
2. **Manual re-upload:** a menu item for when the user thinks their data on the server is wrong.

`stats.year_stats(col)` already computes exactly the per-day rows needed, bucketed by the scheduler rollover just like `today_stats`. Reuse it rather than writing new SQL.

Read `CLAUDE.md`, `README.md` and `API_GUIDE.md` first. Commands: `uv sync`, `uv run pytest`, `uv run ruff check .`, `uv run pyright`.

## Ground rules

- Only `ui.py` (and dialog modules) imports `aqt`. Decisions go in a pure module (`history_logic.py`) so they can be tested without Anki.
- Read the collection through `aqt.operations.QueryOp`, never from a bare background thread. Do the network call afterwards through `mw.taskman.run_in_background`. Don't hold the collection during a network request.
- Never log tokens, passwords or review contents. Logging counts and dates is fine.
- Every change gets tests in the existing style (`httpx.MockTransport`, no network).
- Update `API_GUIDE.md`, `README.md`, `config.md` and `CLAUDE.md` when behavior changes.
- Ask before making any design decision that isn't covered here.

## The one real risk: uploading an empty collection

Because the server **replaces** the range, uploading from a collection that hasn't synced yet would wipe the user's server history. That includes a fresh Anki install on a new computer, or a profile that hasn't pulled from AnkiWeb. Every task below has a guard for this:

- **Wait for sync.** If the profile uses AnkiWeb sync, the automatic backfill waits until the first `sync_did_finish` of the session, so the collection includes mobile reviews and other devices.
- **Never auto-upload an empty year.** If `year_stats` finds zero reviews in the whole range, the automatic backfill is skipped and stays pending. It doesn't set the server flag, so it tries again next session.
- **Warn before a manual empty upload.** The manual re-upload still allows an empty year, since clearing history might be what the user wants, but only behind an explicit warning.

---

## Task 1: API client and models

1. **In `models.py`,** add frozen dataclasses with `from_dict`, following the existing models:
   - `HistoryResult`: `start`, `end`, `days_written`, `history_synced_at`.
   - `HistoryStatus`: `history_synced_at: datetime | None`.

2. **In `ApiClient`,** add:
   - `get_history_status() -> HistoryStatus`, which calls `GET /reviews/history/status`.
   - `upload_history(history: YearReviews) -> HistoryResult`, which calls `PUT /reviews/history` with `start`, `end` and every day (`study_date`, `total_reviews`, and `retention`/`study_minutes` when not `None`). PUT is already retried by default, which is safe because the endpoint is idempotent.
   - Map 429 to `ApiError(429, "Uploaded too many times recently. Try again in an hour.")`, and 422 to `ApiError(422, "The server rejected this history: <detail>")`.
   - A 404 from either call means the backend is older than this feature. Raise a dedicated `FeatureUnavailableError`, a subclass of `AnkiHelperError`, so the automatic path can skip it quietly.

**Tests:** request body shape (with `None` stats left out), parsing both responses, the 404/422/429 mapping, retry on 5xx, and that the bearer token is sent.

## Task 2: Pure decision logic, in `history_logic.py`

Keep every decision here so it's testable:

- `should_auto_backfill(status: HistoryStatus | None, already_done_this_session: bool) -> bool` is true only when the server says `history_synced_at` is `None` and we haven't already done it this session.
- `has_reviews(history: YearReviews) -> bool` is true if any day has `total_reviews > 0`.
- `manual_confirmation(history: YearReviews, synced_this_session: bool) -> str` returns the confirmation text for the manual dialog:
  - It always explains that the last year on the server will be replaced with this computer's data, and that the user's groups will see the result.
  - If the collection hasn't synced this session, add: "Sync with AnkiWeb first so reviews from your phone are included."
  - If `has_reviews` is false, add a strong warning: "This computer has no reviews in the last year. Uploading will clear your history on the server."
- `result_message(result: HistoryResult) -> str` returns, for example, "AnkiHelper: uploaded 213 days of review history."

**Tests:** cover every branch above.

## Task 3: Automatic backfill

In `Controller` in `ui.py`:

1. **Track state.** Keep `self._synced_this_session: bool`, set in `on_sync_finished`, and `self._auto_backfill_done: bool`.

2. **Add `maybe_auto_backfill()`**, called on the main thread:
   - Return early if the user isn't logged in or it's already done this session.
   - If the profile uses AnkiWeb sync and hasn't synced yet this session, return. It will be called again from `on_sync_finished`. Check how this Anki version exposes "sync is set up" (`mw.pm.sync_auth()` returning `None` when logged out, in recent versions), and confirm with me if it's different.
   - Otherwise, in the background, fetch `get_history_status()`. If `should_auto_backfill` is false, stop.
   - Read `year_stats(col)` through a `QueryOp`. If `has_reviews` is false, log and stop, without marking it done.
   - Upload in the background. On success:
     - set `_auto_backfill_done`
     - show `result_message` as a tooltip
     - call `self.groups.mark_stale()` so the group heatmaps reload
     - set `self._last_pushed` to today's row from the upload, so the next `push_today` isn't redundant
   - On `FeatureUnavailableError`, `NetworkError` or `ServerError`, log at info level and do nothing else. It retries next session. Never show a warning dialog from the automatic path; it's background behavior.

3. **Call it from:**
   - the restore-session success handler, at startup.
   - the login and signup success handlers. This covers new users, and existing users like your current testers, who also have a `null` flag and get backfilled once.
   - `on_sync_finished`, after `push_today`.

   Guard against overlapping runs with an in-flight flag, the same pattern as `GroupLoader._inflight`.

**Tests:** if the controller can't be tested without `aqt`, pull the sequencing into `history_logic.py` as a small state machine with injected callables, like `GroupLoader` does. Test that:
- a user who hasn't synced waits for sync,
- a user without AnkiWeb sync runs immediately,
- a non-null flag skips the upload,
- an empty year skips the upload and stays pending,
- errors stay silent and pending,
- a second call while one is in flight does nothing.

## Task 4: Manual re-upload

1. **Add the menu item.** Under Tools > AnkiHelper, add "Re-upload review history…", next to Log in and Log out. If the user isn't logged in, show the existing "please log in" message.

2. **Add the flow:**
   - Read `year_stats(col)` through `QueryOp`.
   - Show `askUser(manual_confirmation(...))`. Use a stronger confirm, such as typing "upload", when `has_reviews` is false, following the delete-group dialog's pattern.
   - Upload in the background, then show `result_message` as a tooltip and call `groups.mark_stale()`.

3. **Handle errors.** This path is user-initiated, so errors **do** show a warning dialog with the mapped message. The 429 message tells them to wait an hour.

**Tests:** the confirmation text is covered in Task 2. Add a client test that a manual upload with an empty `days` list sends a valid body.

## Task 5: Catch-up after each sync (recommended)

The automatic backfill only runs once per account. Phone reviews from a few days ago will still be missed after that, unless they get swept up on sync.

On every `on_sync_finished`, after the automatic backfill check, upload the last **7** days through the same endpoint. Seven days is a small payload and covers a week of phone-only studying. Apply the same rules:
- skip if `has_reviews` is false for those days.
- stay silent on errors.
- don't run if the automatic backfill just ran this session, since that already covered it.

Count this against the backend's 5-per-hour limit. Sync usually happens a few times a day, so it fits, but skip the catch-up if the last one was under 15 minutes ago.

**Ask me before starting this task.** It changes the request rate per user, and I may want the backend limit adjusted first.

## Task 6: Docs and smoke test

1. **Docs:**
   - **`API_GUIDE.md`:** both endpoints, and how the add-on uses them.
   - **`README.md`:** add a feature-list line: "Uploads your past year automatically the first time, and after each sync catches up the last week."
   - **`config.md`:** only if a new config key was added. The plan doesn't need one, since the flag lives on the server.

2. **Smoke test in a real Anki** before release:
   - a fresh account with existing review history: the automatic backfill runs after sync, and the group heatmap fills in.
   - logging in on a second profile with an empty collection: the automatic backfill is skipped.
   - a manual re-upload: the confirmation shows, the upload succeeds, and the tooltip appears.
   - a manual re-upload with no reviews: the strong warning shows.
   - reviews done on a phone and then synced: they appear after the catch-up (Task 5).

   Record the results in the roadmap's progress table.
