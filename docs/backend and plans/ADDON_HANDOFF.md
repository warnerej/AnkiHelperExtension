# Handoff: backend → Anki add-on

## Context
The backend (`AnkiHelper` repo, branch `add-groups`) now supports groups and account deletion. Sharing moves from per-user friends to groups: joined members see each other's daily reviews. This is what the add-on can build against. Full request/response detail is in `README.md` and `docs/API_GUIDE.md`; this summarises it. (`docs/ANKI_HANDOFF.md` is stale, ignore it.)

## Roadmap status
| Task | Status |
| --- | --- |
| 0. Leaked-secrets check | No record in the repo (not verified here). Confirm separately, rotate keys if needed. |
| 1. Groups | Done (migrations 0004, 0005, 0006) |
| 2. Account deletion | Done (migration 0007) |
| 3. Website | Not started (no CORS, no site repo). Must include the password-reset page: see `password-reset-handoff.md` |
| 4. Password reset | Add-on and backend (`POST /auth/forgot-password`, migration 0009) done; site page pending |

Migrations 0001-0009 are applied by hand in Supabase, in order; the add-on needs them applied on the live project.

## Auth (unchanged, plus one endpoint)
Login with email/password → `access_token`, `refresh_token`; send `Authorization: Bearer <token>`; 401 → `/auth/refresh` → relogin. Signup requires a `username` (3-20 chars: letters, digits, `_`; stored lowercase; 409 if taken).
New: `DELETE /auth/me` body `{password}` → 204; 403 wrong password. Irreversible.

## Endpoints the add-on can use
Daily reviews (unchanged): `PUT /reviews/daily`, `GET /reviews/daily`, `GET /reviews/daily/{date}`.

Groups (all Bearer):
- `POST /groups {name}` → 201, group incl. `invite_code` (max 20 owned → 409)
- `GET /groups` → `[{id, name, role, member_count}]`
- `GET /groups/{id}` → `{id, name, owner, members:[{username, role, joined_at}]}`; owner also gets `invite_code`, `allowed`. 404 for non-members
- `PATCH /groups/{id} {name}` (owner) · `DELETE /groups/{id}` (owner, 204)
- `POST /groups/join {invite_code}` → group. 404 for bad code *and* not-on-allowed-list (identical). 429 after 10 failed attempts/hour. Joining twice is fine
- `POST /groups/{id}/leave` → 204; 409 for owner
- `POST /groups/{id}/allowed {username}` (owner; max 50 → 409; user needn't exist) · `DELETE /groups/{id}/allowed/{username}` (owner, 204, also removes membership; 409 for owner's own name)
- `POST /groups/{id}/invite-code` (owner) → `{invite_code}`; old code dies
- `POST /groups/{id}/transfer {username}` (owner; target must have joined, else 409)
- `GET /groups/{id}/reviews?days=30` (1-365) → `{group_id, start, end, members:[{username, days:[...]}]}`; every joined member listed, `days` may be empty; 404 for non-members

## Behaviour to design the UI around
- Being on the allowed list shares nothing; only joined members share. Joined members see the last 365 days, including pre-join days.
- Removing from the allowed list or leaving cuts access both ways immediately.
- Owner can't leave; must transfer or delete. On account deletion, owned groups pass to the longest-standing member, or are deleted if empty.
- Handle 404 (bad code / not allowed look the same), 409 (limits, owner leave), 429 (join rate limit) in the join dialog.

## Deprecated
`GET /profiles/{username}/reviews` still works but returns 404 unless both users joined a common group. Switch the add-on to `GET /groups/{id}/reviews`; the old route will be removed once the new add-on ships.

## Not done / caveats
- `groups.plan` column exists but isn't enforced. No billing yet.
- No CORS (only matters for the website).
- Not re-run in this session: `uv run pytest`. Run it before handing over.
