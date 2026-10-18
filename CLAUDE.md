# AnkiHelperExtension

Anki add-on (client) for AnkiHelper. It reads study stats from the local Anki collection, pushes them to the AnkiHelper FastAPI backend (Supabase-backed), and pulls stats back to display progress for the user and others. Backend context: `ANKI_HANDOFF.md`.

**Status:** backend has auth (`/auth/signup|login|refresh|me|logout`), daily reviews (`PUT/GET /reviews/daily`, `GET /reviews/daily/{date}`) and friends (`GET /profiles/{username}/reviews`, past year via `days=365`, 404 if unknown). The add-on implements login/refresh, signup, daily push and the friends fetch (usernames come from the `friends` config list; no friends-list endpoint exists). Endpoint details: `API_GUIDE.md`.

## Environment
- `uv` project. Python is pinned to **3.13** because Anki bundles its own interpreter (not 3.14). Verify against the installed Anki (Help > About) before changing.
- `uv sync`, `uv run pytest`, `uv run ruff check .`, `uv run pyright`.
- Dev-only deps: `anki`, `aqt`, `pytest`, `ruff`, `pyright`. Anki provides these at runtime; never vendor them.
- Runtime deps (`httpx`, `pydantic`) are not installed by Anki, so they get vendored into `addon/vendor/` at package time (`uv pip install --target addon/vendor ...`). Keep pure-Python or match Anki's platform wheels.

## Layout (planned)
- `addon/` is what gets zipped to `.ankiaddon`: `__init__.py` (hooks), `api_client.py`, `stats.py`, `ui.py`, `config.json`, `vendor/`.
- `tests/`, `scripts/package.sh`.

## Auth flow the client implements
1. `POST /auth/login` with email/password from add-on config -> `access_token`, `refresh_token`, `expires_in` (3600s).
2. Keep tokens in memory only. Send `Authorization: Bearer <access_token>`.
3. On 401: `POST /auth/refresh`, retry once. If refresh fails, log in again.
4. Refresh tokens rotate and are single use. Never refresh before every call.

## Conventions
- Never embed or log passwords/tokens. Never use a Supabase service-role key. The add-on talks only to the AnkiHelper API, not Supabase directly.
- Network calls run off the main thread (`mw.taskman.run_in_background`); never block the Qt UI.
- Read the collection through Anki's APIs; never write to the collection DB directly.
- Sync must be idempotent and incremental (e.g. cursor = last revlog id).
- Tests use a fake HTTP transport (`httpx.MockTransport`) and temp `Collection` fixtures; no network.

## Unresolved (settle with the user before coding)
What is sent/received, table schema and RLS, sync/conflict semantics, id mapping (Anki ids -> server ids), and any external services.
