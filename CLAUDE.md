# AnkiHelperExtension

Anki add-on (client) for AnkiHelper. It reads study stats from the local Anki collection, pushes them to the AnkiHelper FastAPI backend (Supabase-backed), and pulls stats back to display progress for the user and others. Endpoint reference: `API_GUIDE.md`; overview and setup: `README.md`.

**Status:** backend has auth (`/auth/signup|login|refresh|me|logout`), daily reviews (`PUT/GET /reviews/daily`, `GET /reviews/daily/{date}`) and friends (`GET /profiles/{username}/reviews`, past year via `days=365`, 404 if unknown). The add-on implements login/refresh, signup, daily push and the friends fetch (usernames come from the `friends` config list; no friends-list endpoint exists).

## Environment
- `uv` project. Python is pinned to **3.13** because Anki bundles its own interpreter (not 3.14). Verify against the installed Anki (Help > About) before changing.
- `uv sync`, `uv run pytest`, `uv run ruff check .`, `uv run pyright`.
- Dev-only deps: `anki`, `aqt`, `pytest`, `ruff`, `pyright`. Anki provides these at runtime; never vendor them.
- Runtime dep `httpx` (and its dependencies) is not installed by Anki, so it gets vendored into `addon/vendor/` at package time (`uv pip install --target addon/vendor ...`). Vendor pure-Python packages only (no compiled extensions) so one build works on every OS; response models are stdlib dataclasses for this reason.

## Layout
- `addon/` is what gets zipped to `.ankiaddon`. Only `ui.py` imports `aqt`; everything else is plain Python so tests run without Anki.
  - `__init__.py` (entry; sets up `vendor/` path, calls `ui.setup()` inside Anki), `ui.py` (`Controller`: menus, hooks, daily push, signup)
  - `friends_loader.py` + `friends_state.py` (friends fetch/retry state machine, with Anki bits injected; pure decisions)
  - `api_client.py` (endpoints, retries), `session.py` (tokens, login/refresh), `settings.py`, `models.py`, `errors.py`, `log.py`
  - `stats.py` (revlog -> stats), `heatmap.py` (HTML/SVG rendering)
  - `config.example.json`, `config.md`, `manifest.json`, `vendor/` (git-ignored, built by packaging)
- `tests/`, `scripts/package.sh`.

## Auth flow the client implements
1. `POST /auth/login` with email/password from add-on config -> `access_token`, `refresh_token`, `expires_in` (3600s).
2. Keep tokens in memory only (the password itself is read from the add-on config, where it is stored in plain text). Send `Authorization: Bearer <access_token>`.
3. On 401: `POST /auth/refresh`, retry once. If refresh fails, log in again.
4. Refresh tokens rotate and are single use. Never refresh before every call.

## Conventions
- Never embed or log passwords/tokens. Never use a Supabase service-role key. The add-on talks only to the AnkiHelper API, not Supabase directly.
- Network calls run off the main thread (`mw.taskman.run_in_background`); never block the Qt UI.
- Read the collection through Anki's APIs; never write to the collection DB directly.
- Sync must be idempotent: each push is an upsert of the day's running total, so re-sending is safe.
- Tests use a fake HTTP transport (`httpx.MockTransport`) and temp `Collection` fixtures; no network.

## Decisions
- Sent: one running-total row per Anki day (`PUT /reviews/daily`, upsert keyed on user + `study_date`; the client owns the date). Received: friends' past year, by username from config.
- Ids: no Anki ids are sent; the server key is `(user, study_date)`. Nothing is deleted server-side.
- Not built: a friends-list endpoint, card/note sync, external services.
