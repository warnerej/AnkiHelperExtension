# AnkiHelper: handoff for building the Anki section

You are picking up a project in progress. This file is the context you need. Read `CLAUDE.md` and `README.md` in the repo root as well. They are authoritative where they overlap with this file.

## What exists

AnkiHelper is a FastAPI backend (Python 3.14, `uv`) with Supabase as the auth provider and data store. The client is an Anki add-on that will call this API. **Only auth is built.** There are no Anki-related routes, models, services or tables yet. That is the work to do.

Layout:

- `app/main.py`: creates the app, includes `auth.router`, and has `GET /health`.
- `app/config.py`: pydantic-settings `Settings` (`supabase_url`, `supabase_key`) loaded from `.env`. `.env.example` lists `SUPABASE_URL` and `SUPABASE_KEY` (anon key).
- `app/routers/auth.py`: `/auth/signup`, `/login`, `/refresh`, `/me`, `/logout`.
- `app/services/auth.py`: `AuthService`, a thin wrapper over Supabase Auth. It maps Supabase errors to `HTTPException`.
- `app/models/auth.py`: pydantic schemas (`Credentials`, `TokenResponse`, `UserResponse`, ...).
- `app/dependencies.py`: shared FastAPI dependencies (see below).
- `tests/test_auth.py`: tests that override `get_auth_service` with a fake, so no Supabase project is needed.

## How auth works (what your routes plug into)

1. The add-on calls `POST /auth/login` with the email and password from its config and gets `access_token`, `refresh_token` and `expires_in` (default 3600s).
2. It keeps tokens in memory and sends `Authorization: Bearer <access_token>` on every other call.
3. On a 401 it calls `POST /auth/refresh`, then retries once. If the refresh fails, it logs in again. Refresh tokens rotate and are single use. The client must not refresh before every call.
4. The server validates the token on each request via `auth.get_user(token)`, which asks Supabase.

## Conventions to follow in new code

- **Protect routes** with `CurrentUser` (`app/dependencies.py`), e.g. `def route(user: CurrentUser)`.
- **Database access** goes through `UserClient`. It is a Supabase client with `postgrest.auth(token)` set, so Postgres Row Level Security applies as that user. Use `client.table("...")` through it.
- **Never add the service-role key.** Only the anon key is used. Isolation between users must come from RLS policies, not from Python filtering.
- A new Supabase client is created per request because the client holds session state in memory. Don't cache or share one.
- Keep the layering: `routers/` (HTTP), `services/` (logic), `models/` (schemas). Match the style of `routers/auth.py` and `services/auth.py` (sync `def` routes, services that take a `Client`, errors raised as `HTTPException`).
- **Tests:** override dependencies with fakes, as in `tests/test_auth.py`, so tests need no network. Run `uv run pytest`.
- Add dependencies with `uv add <pkg>`. There is no linter configured.

## Open questions to settle with the user before coding

The "Anki section" has not been specified in the repo. Ask the user, don't guess:

1. **What does the add-on send and receive?** Examples: card or note sync, AI-generated cards, deck stats, review history, or study suggestions.
2. **Data model:** which Supabase tables are needed (e.g. decks, notes, cards) and what are their columns? The schema and RLS policies need to be written. Every table should carry a `user_id` that defaults to `auth.uid()`, with policies restricting rows to their owner.
3. **Sync semantics:** is the server the source of truth or is Anki? How are conflicts and deletions handled, and is sync incremental (e.g. an `updated_at` cursor)?
4. **Identifiers:** how are Anki's local note and card ids mapped to server ids?
5. **Any external services** (e.g. an LLM provider) and where their keys live.

## Suggested approach
/
1. Get answers to the questions above.
2. Write the SQL (tables and RLS policies) as a migration file. The user applies it in Supabase.
3. Add `app/models/<feature>.py`, `app/services/<feature>.py` and `app/routers/<feature>.py`, then register the router in `app/main.py`.
4. Add tests with fake dependencies.
5. Update `README.md` (endpoint table and add-on flow) and `CLAUDE.md` (status line).

## Verification

- `uv run pytest` passes.
- `uv run uvicorn app.main:app --reload`, then log in via `/auth/login` and exercise the new endpoints with the bearer token (needs a real `.env`).
- Confirm RLS: a second user cannot read or write the first user's rows.
