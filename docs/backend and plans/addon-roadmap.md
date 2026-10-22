# AnkiHelper add-on roadmap: groups and account deletion

Handoff for Claude Code, for the **add-on repo** (`AnkiHelperExtension`). Work through the tasks **in order** and stop for review after each one.

**Depends on the backend:** these tasks use new endpoints from the backend roadmap (groups and `DELETE /auth/me`). Start only once those are deployed, and use the backend's `/docs` page or its updated `API_GUIDE.md` as the source of truth if anything here disagrees.

## Context

The add-on pushes daily review totals and draws year heatmaps. Already done: login, signup and logout dialogs with only the refresh token stored in `user_files/` (no password on disk), and pydantic replaced by dataclasses with `from_dict` so the add-on works on every OS.

Sharing is moving from a per-user `friends` list to **groups ("rooms")**. A group owner adds allowed usernames, and an allowed user joins by entering the group's invite code. Everyone who has joined a group sees every member's heatmap and shares their own.

Read `CLAUDE.md`, `README.md` and `API_GUIDE.md` first. Commands: `uv sync`, `uv run pytest`, `uv run ruff check .`, `uv run pyright`.

## Ground rules

- Only `ui.py` (and any new dialog modules) imports `aqt`. Keep logic in pure modules so tests run without Anki.
- Network calls run off the main thread through `mw.taskman.run_in_background`. Never block the UI, and never log tokens or passwords.
- New response types are frozen dataclasses with `from_dict`, following the existing models.
- Every change gets tests in the existing style (`httpx.MockTransport`, no network).
- Update `API_GUIDE.md`, `README.md`, `config.md` and `CLAUDE.md` when behavior changes.
- Ask before making any design decision that isn't covered here.

---

## Progress

| Task | Status |
| --- | --- |
| 1. Groups API client | **Done** (committed) |
| 2. Groups screen | **Done** (committed; dialog not exercised in a live Anki yet) |
| 3. Group heatmaps | **Done** (committed; not exercised in a live Anki yet) |
| 4. Delete account | **Done** (committed; dialog not exercised in a live Anki yet) |
| 5. Release prep | **Next** (packaging and CI done; README screenshots, AnkiWeb listing and privacy policy remain) |
| Backfill 1. History client | **Done** (committed) |
| Backfill 2. Pure logic (`history_logic.py`) | **Done** (committed) |
| Backfill 3. Automatic backfill (`history_backfill.py`, wired in `ui.py`) | **Done** (committed; Qt wiring not run in a live Anki) |
| Backfill 4. Manual re-upload menu item | **Done** (committed; not run in a live Anki) |
| Backfill 5. Post-sync 7-day catch-up | **Done** (committed; approved by user; only runs once the server already has history) |
| Backfill 6. Docs | **Done** (`API_GUIDE.md`, `README.md`, `CLAUDE.md`) |
| Backfill smoke test in a real Anki | **Pending** (see checklist below) |

Tasks 1-4 and the backfill are committed. None of the Qt code (`groups_dialog.py`, `ui.py`) has been exercised in a live Anki, so a manual smoke test is still owed.

### Task 4 outcome

`ApiClient.delete_account(password)` sends `DELETE /auth/me {password}` through `request(..., retries=0)`; `request` now takes a keyword-only `retries` override (`None` = per-method default). 403 becomes `ApiError(403, "Wrong password.")`. On success `Session.account_deleted()` clears memory and the token file without calling `/auth/logout`. Decision: the confirmation text is the account **email**, fetched via `get_me()` when the menu item is used (no backend change needed). Pure checks are in `delete_logic.py`; `DeleteAccountDialog` and `Controller.delete_account/_on_delete_result` are in `ui.py`. On success the Groups dialog is closed and `GroupLoader.reset()` clears state (a generation counter makes a fetch already in flight get ignored) and the stored `selected_group`.

### Before starting Task 4 (kept for reference)

Read the outcomes below, then:

- **Open question: where does the username come from?** The confirmation step needs the user to type their username, but `GET /auth/me` returns only `{id, email}` (`UserResponse`) and the add-on stores no username (the old `username` config key was removed as a legacy credential). Options: ask the backend to add `username` to `/auth/me` (check the live `/docs` first, it may already be there), or have the dialog ask the user to type the email instead. Ask the user which before building.
- **Client call:** add `delete_account(password)` to `ApiClient`/`Session`: `DELETE /auth/me` with JSON body `{password}`, 204 on success, 403 for a wrong password (map to a clear message such as "Wrong password"). `ApiClient.request(method, path, **kwargs)` (`api_client.py`) forwards kwargs such as `json=` to httpx and handles the 401-refresh-retry through `_request_once`/`Session`. Its retry count is derived from the method (`DELETE` retries on network errors and 5xx), but this call is irreversible and sends a password, so it should not retry: add a way to opt out (e.g. a `retries` override) and test that a 5xx or network error is attempted once.
- **After success:** clear the token file and in-memory tokens (`Session._clear` / `TokenStore.clear`, as `logout` does but without calling `/auth/logout`, since the account is gone), and reset the Groups tab (`GroupLoader` state) because every group membership is gone. `Controller.groups` holds that state; a `reset()` method may be needed.
- **UI pattern to follow:** `CredentialsDialog` in `ui.py` (password field with `QLineEdit.EchoMode.Password`, validation before `accept`), `Controller.log_in/_on_login_result` for the background-call and error pattern, and `groups_logic.py` for putting the confirmation decisions (typed username matches, password non-empty, message text) in a pure, tested module. Add the menu item next to Log out in `setup()`.
- **Never log or store the password.** `log.py` redacts, but do not rely on it; do not put the password in exception messages or the logged request body.
- Docs to update when done: `API_GUIDE.md` (add `DELETE /auth/me`), `README.md` (Tools menu list and endpoint table), `CLAUDE.md` (Status and Layout), this file.
- Backend semantics (from `ADDON_HANDOFF.md`): irreversible; owned groups pass to the longest-standing member, or are deleted if empty; 403 on a wrong password.

### Task 3 outcome

Friends are gone: `get_profile_reviews`, `get_friends_reviews`, `Settings.friends` and `ProfileReviews.from_dict` are removed. `FriendDay` is now `ReviewDay`; `ProfileReviews` is now `YearReviews` (plain dataclass, the heatmap's input). `GroupReviews.year_for(member)` and `.total()` build them. `group_loader.py` has `GroupLoader` plus `load_snapshot` (list groups, then the selected group's reviews; falls back to the first group if the remembered one is gone). The deck-browser view is `"groups"`, the picker sends `GROUP_CMD`, and the selection is stored in config as `selected_group`. Closing the Groups dialog marks the loader stale so changes show up. `strip_friends` removes an old `friends` list at startup and shows a one-time note. The optional Group total heatmap is included (summed reviews and minutes, no combined retention).

### Task 2 outcome

`groups_logic.py` (pure, `tests/test_groups_logic.py`) and `groups_dialog.py` (Qt, non-modal, single instance held by `Controller.open_groups`, closed on profile close). Menu item "Groups..." plus a "Manage groups" button on the Groups tab (`GROUPS_CMD` in `heatmap.py`). After a `NetworkError` on any write the dialog re-fetches the list. `ApiError` is shown via `.detail` (no status prefix). Group-name max length (50) is a guess; check the backend's limit.

### Task 1 outcome

Everything is in `addon/api_client.py` (`ApiClient`, blocking, call from `mw.taskman.run_in_background` only) and `addon/models.py`. Endpoint detail is in `API_GUIDE.md`. The backend summary is in `ADDON_HANDOFF.md` in this folder.

| Method | Returns |
| --- | --- |
| `list_groups()` | `list[GroupSummary]` (`id, name, role, member_count`) |
| `get_group(id)` | `Group` (`id, name, owner, members: list[GroupMember(username, role, joined_at)]`; `invite_code` and `allowed` are `None` unless the caller owns it) |
| `create_group(name)`, `rename_group(id, name)`, `join_group(invite_code)` | `Group` |
| `delete_group(id)`, `leave_group(id)`, `allow_username(id, u)`, `disallow_username(id, u)`, `transfer_ownership(id, u)` | `None` |
| `regenerate_invite_code(id)` | the new code as `str` |
| `get_group_reviews(id, days=365)` | `GroupReviews(group_id, start, end, members: list[MemberReviews(username, days: list[FriendDay])])` |

Behaviour to design the UI around:
- Errors are `AnkiHelperError` subclasses with user-safe messages. 4xx raise `ApiError` (has `.status_code` and `.detail`). `join_group` already rewrites 404 ("Code not found, or your username isn't on this group's allowed list.") and 429 ("Too many attempts. Try again later."). `leave_group` rewrites 409 ("Owners can't leave; transfer ownership or delete the group."). Other 409s (20 owned groups, 50 allowed usernames, transfer target not joined, removing the owner's own name) pass the server's `detail` through. Show `str(exc)` or `exc.detail`.
- Retries: GET, PUT and DELETE retry on network errors and 5xx; POST and PATCH never do. So a failed create, join, leave, allow, transfer or rename needs a user-driven retry, and the UI must not assume it did not reach the server after a `NetworkError`; refresh the list after one.
- Removing a username (`disallow_username`) also removes that member server-side, and removal or leaving cuts access both ways immediately. Being on the allowed list shares nothing; only joined members share.
- The server's `owner` field is a username, `role` is `"owner"` or `"member"`. `GroupSummary.role` is the way to decide owner versus member controls for the list; for details use `Group.invite_code is not None`.
- Group ids and usernames are URL-quoted by the client; pass them raw.
- The friends code (`get_profile_reviews`, `get_friends_reviews`, `friends_loader.py`, `friends_state.py`, the `friends` option) was removed in Task 3.

Notes for later tasks: `FriendDay` was renamed `ReviewDay` in Task 3. `tests/conftest.py`'s `FakeBackend` accepts `(status, None)` for body-less responses such as 204. Existing UI patterns live in `addon/ui.py` (`Controller`: menus, login/signup dialogs, hooks).

---

## Task 1: Groups API client (done, kept for reference)

Add methods to `api_client.py`, with dataclass models for each response:

- `list_groups()` → `GET /groups`
- `get_group(id)` → `GET /groups/{id}` (`invite_code` and `allowed` are present only for the owner)
- `create_group(name)`, `rename_group(id, name)`, `delete_group(id)`
- `join_group(invite_code)`: map a 404 to a clear "Code not found, or your username isn't on this group's allowed list" error
- `leave_group(id)`: map a 409 to "Owners can't leave; transfer ownership or delete the group"
- `allow_username(id, username)`, `disallow_username(id, username)`
- `regenerate_invite_code(id)`, `transfer_ownership(id, username)`
- `get_group_reviews(id, days=365)` → `{group_id, start, end, members: [{username, days}]}`

Keep the existing retry rules: GET, PUT and DELETE retry on network errors and 5xx; POST and PATCH never retry.

---

## Task 2: Groups screen (done, kept for reference)

A Qt dialog opened from **Tools > AnkiHelper > Groups…**, and from the heatmap's Groups tab:

- **My groups list:** name, my role and member count.
- **Create group** (name) and **Join group** (invite code).
  - Before joining, confirm with the user: "Joining shares your last year of review stats with everyone in this group, and lets you see theirs."
- **Group details:** the member list and a **Leave** button (hidden for the owner).
- **Owner controls:**
  - The invite code, with Copy and Regenerate buttons.
  - The allowed usernames, with Add and Remove. Removing a username also removes that member; confirm first.
  - Rename, **Transfer ownership** (pick a member) and **Delete group** (requires typing the group name to confirm).

Keep the decision logic (what to show for owners versus members, input validation, error messages) in a pure module with tests. Keep `ui.py` thin.

---

## Task 3: Group heatmaps (done, kept for reference)

- Rename the **Friends** tab on the deck browser to **Groups**. Add a group picker (remembered between sessions in the add-on config) and show each member's heatmap for the selected group. Show the user's own heatmap first, as it is now.
- Load with one `get_group_reviews` call per group instead of one call per friend. Adapt `friends_loader.py` and `friends_state.py`, renaming them to `group_loader.py` and `group_state.py`. Keep the current behavior: lazy load when the tab first opens, refresh after 5 minutes, retry every 15 seconds while the server is waking, and keep showing the last data if a background refresh fails.
- Empty states: when the user has no groups, show "Create or join a group" with a button that opens the Groups screen; when a group has only the user, show an invite hint.
- **Optional, small:** a "Group total" heatmap above the members, summing everyone's daily reviews.
- **Remove the `friends` config option** and the per-username friend calls (`get_profile_reviews` and `get_friends_reviews`), and update their tests. The backend keeps the old endpoint temporarily, but the add-on should stop using it.
- **Existing users:** if an old config still has a `friends` list, ignore it, remove it from the config, and show a one-time note that sharing now works through groups.

---

## Task 4: Delete account (done, kept for reference)

- **Tools > AnkiHelper > Delete account…**, with a dialog that:
  - explains that the account, all review history and group memberships are deleted, and that owned groups pass to another member (or are deleted if empty),
  - requires the password and typing the username to confirm,
  - calls `DELETE /auth/me` with `{password}`, then clears the local token file and returns to the logged-out state.
- Show a clear error for a wrong password, and never log the password.
- Tests for the confirmation logic and the client call.

---

## Task 5: Release prep

- Check that packaging (`scripts/package.sh`) produces a working `.ankiaddon`, with only pure-Python packages in `vendor/` and the example config (no local `user_files/`).
- Update the README: screenshots of the Groups tab, cross-platform install steps, and how groups and invite codes work.
- Draft an AnkiWeb listing description: what it does, that an account is required, what data is shared and with whom, and a link to the privacy policy (a placeholder until the website exists).

### Backfill smoke test (pending; record results here)

- [ ] Fresh account with existing review history: backfill runs after sync, tooltip appears, group heatmap fills in.
- [ ] Log in on a second profile with an empty collection: backfill is skipped (log shows "no reviews in the last year").
- [ ] Manual re-upload with reviews: confirmation shows (with the sync note if not yet synced), upload succeeds, tooltip appears.
- [ ] Manual re-upload with no reviews: strong warning and the type-`upload` dialog.
- [ ] Reviews done on a phone, then synced: they appear after the catch-up.
- [ ] Log out and log in as another account in one session: that account is backfilled too.
- [ ] Backend without the endpoints (404): nothing shown from the automatic path; manual shows a message.
