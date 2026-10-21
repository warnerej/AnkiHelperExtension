# AnkiHelper API guide

Reference for the AnkiHelper backend API that the Anki add-on talks to. The backend lives in a separate repo. `README.md` and `CLAUDE.md` in the repo root are authoritative if anything here disagrees. The backend serves interactive docs at `/docs`.

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
| `DELETE /auth/me` | yes | `{password}` | 204; irreversible (deletes reviews and memberships; owned groups pass to the longest-standing member, or are deleted if empty); 403 for a wrong password. The add-on never retries it |
| `PUT /reviews/daily` | yes | `{total_reviews, study_date, retention?, study_minutes?}` | the saved row (below) |
| `GET /reviews/daily` | yes | query: `start`, `end` (ISO dates, optional), `limit` (1-365, default 90) | list of rows, newest first |
| `GET /reviews/daily/{study_date}` | yes | | one row, or 404 |
| `GET /profiles/{username}/reviews` (deprecated; the add-on no longer calls it) | yes | | `{username, start, end, days}` with that user's rows for the past `days` days (query param, 1-365, default 30; the add-on sends 365; newest first, same fields as a row minus `updated_at`); 404 if no such username |
| `POST /groups` | yes | `{name}` | 201 group incl. `invite_code` (409 at 20 owned groups) |
| `GET /groups` | yes | | `[{id, name, role, member_count}]` |
| `GET /groups/{id}` | yes | | `{id, name, owner, members: [{username, role, joined_at}]}`; the owner also gets `invite_code` and `allowed`; 404 for non-members |
| `PATCH /groups/{id}` | owner | `{name}` | group |
| `DELETE /groups/{id}` | owner | | 204 |
| `POST /groups/join` | yes | `{invite_code}` | group; 404 for a bad code or a username not on the allowed list (indistinguishable); 429 after 10 failed attempts per hour |
| `POST /groups/{id}/leave` | member | | 204; 409 for the owner |
| `POST /groups/{id}/allowed` | owner | `{username}` | allowed (max 50 -> 409) |
| `DELETE /groups/{id}/allowed/{username}` | owner | | 204; also removes that member; 409 for the owner's own name |
| `POST /groups/{id}/invite-code` | owner | | `{invite_code}`; the old code stops working |
| `POST /groups/{id}/transfer` | owner | `{username}` | target must have joined, else 409 |
| `GET /groups/{id}/reviews` | member | query: `days` (1-365; the add-on sends 365) | `{group_id, start, end, members: [{username, days}]}`; every joined member is listed, `days` may be empty |

A review row is `{study_date: "YYYY-MM-DD", total_reviews: int, retention: float | null, study_minutes: float | null, updated_at: ISO datetime}`. `retention` is a percent (0-100) and `study_minutes` is the minutes studied that day (0-1440). Both are optional on PUT; if omitted, any stored value is kept (null if never sent). Send them as running values for the day, like `total_reviews`.

## Auth flow for the client

1. The user logs in through a dialog (`POST /auth/login`); the password is never stored. On startup the add-on restores the session with `POST /auth/refresh` using the saved refresh token.
2. Keep the access token in memory only; persist only the refresh token (in `user_files/session.json`, not the config file). Send `Authorization: Bearer <access_token>` on every call.
3. On a 401, call `POST /auth/refresh` once and retry the original request once. Refresh tokens rotate and are single use, so always store the new refresh token, and do not refresh before every call. If the refresh is rejected, delete the saved token and ask the user to log in again.
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
