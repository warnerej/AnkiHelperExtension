# AnkiHelper API guide

For an agent building the client (the Anki add-on) or extending the backend. `README.md` and `CLAUDE.md` in the repo root are authoritative if anything here disagrees. Interactive docs are at `/docs` when the server is running (`uv run uvicorn app.main:app --reload`).

## Endpoints

All bodies and responses are JSON. Every endpoint except `/health`, `/auth/signup`, `/auth/login` and `/auth/refresh` needs the header `Authorization: Bearer <access_token>`.

| Method and path | Auth | Request | Success response |
| --- | --- | --- | --- |
| `GET /health` | no | | status |
| `POST /auth/signup` | no | `{email, password, username}` (password >= 8 chars; username 3-20 chars of letters, digits, `_`, stored lowercase; 409 if taken) | 201 `{user: {id, email}, tokens \| null, confirmation_required}` |
| `POST /auth/login` | no | `{email, password}` | `{access_token, refresh_token, token_type, expires_in}` |
| `POST /auth/refresh` | no | `{refresh_token}` | new `{access_token, refresh_token, token_type, expires_in}` |
| `GET /auth/me` | yes | | `{id, email}` |
| `POST /auth/logout` | yes | | 204, ends this session |
| `PUT /reviews/daily` | yes | `{total_reviews, study_date, retention?, study_minutes?}` | the saved row (below) |
| `GET /reviews/daily` | yes | query: `start`, `end` (ISO dates, optional), `limit` (1-365, default 90) | list of rows, newest first |
| `GET /reviews/daily/{study_date}` | yes | | one row, or 404 |
| `GET /profiles/{username}/reviews` | yes | | `{username, start, end, days}` with that user's rows for the past `days` days (query param, 1-365, default 30; the add-on sends 365; newest first, same fields as a row minus `updated_at`); 404 if no such username |

A review row is `{study_date: "YYYY-MM-DD", total_reviews: int, retention: float | null, study_minutes: float | null, updated_at: ISO datetime}`. `retention` is a percent (0-100) and `study_minutes` is the minutes studied that day (0-1440). Both are optional on PUT; if omitted, any stored value is kept (null if never sent). Send them as running values for the day, like `total_reviews`.

## Auth flow for the client

1. On startup, call `POST /auth/login` with the email and password from the add-on config.
2. Keep both tokens in memory only (not in the config file). Send `Authorization: Bearer <access_token>` on every call.
3. On a 401, call `POST /auth/refresh` once and retry the original request once. Refresh tokens rotate and are single use, so always store the new refresh token, and do not refresh before every call. If the refresh fails, log in again.
4. Use HTTPS in deployment since the password is sent on login and signup.

## Designing review calls

`PUT /reviews/daily` is an idempotent upsert keyed on the user and `study_date`.

- **The client owns the date.** The server does no timezone or day-rollover logic. The add-on must work out which study day it is (for example, treating 00:00 to 03:59 as the previous day, as Anki does with its "next day starts at" setting) and send it as an ISO date, `YYYY-MM-DD`.
- **Send the running total, not a delta.** `total_reviews` overwrites the stored value for that date, so the last PUT wins. To record progress, send today's current total again (for example after each review session or on a timer). Do not send "reviews since last sync".
- **Retries are safe.** Repeating the same PUT gives the same result, so a failed or timed-out call can be retried without double counting.
- **New day, new row.** Sending a different `study_date` inserts a new row. Past dates can be written too, which allows back-filling history.
- **Reading back:** use `GET /reviews/daily?start=...&end=...` for a range, or `GET /reviews/daily/{study_date}` for one day (404 means nothing stored for that date, which is not an error for the client to retry).
- A user only ever sees and writes their own rows. This is enforced by Postgres Row Level Security, so the client never sends a user id.

Example:

```
PUT /reviews/daily
Authorization: Bearer <access_token>
{"total_reviews": 42, "study_date": "2026-01-31", "retention": 87.5, "study_minutes": 31.5}

200 {"study_date": "2026-01-31", "total_reviews": 42, "retention": 87.5, "study_minutes": 31.5, "updated_at": "2026-01-31T21:04:11.2+00:00"}
```

## Error handling

| Status | Meaning | Client action |
| --- | --- | --- |
| 401 | missing, invalid or expired token | refresh once, retry once, else log in again |
| 404 | no review row for that date | treat as "no data" |
| 422 | body or query failed validation (negative total, bad or missing date, short password) | fix the request, don't retry unchanged |
| 502 | the database call failed | retry later with backoff |

## Extending the backend

Follow `CLAUDE.md`. In short:

- Layers: `app/routers/` (HTTP), `app/services/` (logic, takes a Supabase `Client`), `app/models/` (pydantic). Register new routers in `app/main.py`.
- Protect routes with `CurrentUser`. Query Supabase through `UserClient` (from `app/dependencies.py`) so RLS applies as that user. Only the anon key is used. Never add the service-role key.
- New tables go in a numbered file in `supabase/migrations/` (the user applies it in Supabase). Give each table a `user_id uuid not null default auth.uid()`, enable RLS, and add per-operation policies for `user_id = (select auth.uid())`. `0001_daily_reviews.sql` is the template. It uses a `(user_id, study_date)` primary key and an `updated_at` trigger.
- Tests override dependencies with fakes (`get_auth_service`, `get_review_service`), so they need no Supabase project. See `tests/test_reviews.py`. Run `uv run pytest`.
- Update the endpoint tables in `README.md` and this file when adding endpoints.

## Note on the older handoff doc

`docs/ANKI_HANDOFF.md` was written before the reviews endpoints existed and says only auth is built. Treat it as background on conventions and open questions. This file and the README describe the current state.
