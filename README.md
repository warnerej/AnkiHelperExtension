# AnkiHelperExtension

![Python 3.13](https://img.shields.io/badge/python-3.13-blue)
![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue)
![Anki add-on](https://img.shields.io/badge/Anki-add--on-informational)

An Anki add-on that turns private study stats into a shared, social progress view. It reads review history from the local Anki collection, pushes daily totals to the AnkiHelper backend (FastAPI + Supabase), and draws a GitHub-style year heatmap of your reviews, and your groups', on Anki's deck browser.

> Backend: https://github.com/warnerej/AnkiHelper (separate repository). The add-on needs a running backend to sync.

## Install

Install from AnkiWeb (listing link to be added once published), or build `dist/ankihelper.ankiaddon` with `scripts/package.sh` and use Tools > Add-ons > Install from file. Requires Anki 26.5 or newer (Python 3.13). Restart Anki, then open Tools > AnkiHelper to sign up or log in. You are asked for consent to share before anything is uploaded; change your mind any time under Tools > AnkiHelper > Sharing. Tools > AnkiHelper > Delete account permanently removes your server-side data.

## Screenshots

| My stats | Groups |
| --- | --- |
| ![My heatmap](docs/images/heatmap-mine.png) | ![Groups heatmap](docs/images/heatmap-groups.png) |

## What is shared

Sharing is opt-in: nothing is uploaded until you accept the consent dialog, and you can stop any time from Tools > AnkiHelper > Sharing. Uploaded: email, username, and per day for the last 365 days the review count, retention and minutes studied. Members of groups you join see your username and those stats. Card contents, deck names and review times are never uploaded. See the [privacy policy](docs/PRIVACY.md) and [terms](docs/TERMS.md).

## How it works

```mermaid
flowchart LR
    A[Anki collection<br/>revlog] -->|stats.py| B[Daily totals]
    B -->|ApiClient + Session| C[(AnkiHelper API<br/>FastAPI + Supabase)]
    C -->|GET /groups/{id}/reviews| D[group_loader.py]
    A -->|year_stats| E[heatmap.py]
    D --> E
    E -->|deck_browser_will_render_content| F[Deck browser]
```

**Consent.** Sharing is opt-in. `consent_logic.py` holds the wording and the pure checks (`has_consent`, `with_consent`); the `sharing_consent` config key starts `false`. `ui.py` asks after login or signup and on a manual push or re-upload, and gates the daily push (including the final push when the profile closes), the history backfill and the re-upload on it. Opting out stops uploads but does not delete server data; Delete account does.

**Daily push.** `stats.today_stats` reads the `revlog` table through Anki's collection API and computes review count, true retention (share of review-type answers that were not "Again") and study minutes. The study day follows Anki's "next day starts at" rollover (`col.sched.day_cutoff`) rather than midnight, and is dated from noon to avoid DST errors. `PUT /reviews/daily` sends the running total for the day, so it is an idempotent upsert and safe to repeat. Pushes fire after answering cards (throttled to once per 5 minutes), after a sync, when the profile closes (skipped if that would need a token refresh; the next start pushes it), or manually from the Tools menu.

**Review history.** The daily push only covers today, so the add-on also uploads past days with `PUT /reviews/history` (which replaces the date range). It uploads your past year automatically the first time (after the first AnkiWeb sync of the session, never from a collection with no reviews), and after each sync catches up the last week so reviews done on your phone show up. "Re-upload review history..." in the Tools menu redoes the year on demand. The decisions are in `history_logic.py` and the sequencing in `history_backfill.py` (both tested without Anki).

**Authentication.** Log in or sign up from Tools > AnkiHelper. The password is sent once and never stored; the access token stays in memory and only the refresh token is saved, atomically, to `user_files/session.json` (which survives add-on updates). On startup the session is restored from that token. Tokens are refreshed shortly before they expire. On a 401 the client refreshes once and retries once; if the refresh is rejected the file is cleared and the user is asked to log in again. Log out calls `POST /auth/logout` and deletes the file. Refresh tokens rotate and are single use, so refreshes are single-flight: when several threads hit a 401 together, only one refresh is sent and the rest reuse its result.

**Groups screen.** Tools > AnkiHelper > Groups... (or "Manage groups" on the Groups tab) opens a non-modal dialog. It lists your groups (name, role, member count) and lets you create a group or join one with an invite code (after a confirmation that joining shares your last year of stats). Members can see the member list and leave; owners also get the invite code (copy/regenerate), the allowed-username list (removing a name also removes that member), rename, transfer ownership and delete (type the group name to confirm). The decisions live in `groups_logic.py` (tested); `groups_dialog.py` is the thin Qt layer and all calls run off the main thread.

**Groups and heatmaps.** Your own year is computed locally from `revlog`. The Groups tab has a picker (the choice is remembered in the `selected_group` config key) and shows a Group total heatmap followed by one heatmap per member, from one `GET /groups/{id}/reviews?days=365` call per group. With no groups it offers to open the Groups screen; with only you in the group it shows an invite hint. Data loads lazily the first time the Groups tab is opened, refresh after 5 minutes, and while the server is down or waking up the add-on retries every 15 seconds. A failed background refresh keeps the previous data on screen. The heatmap is a Monday-first SVG grid with 10 colour levels scaled to each person's own data, plus daily average, days-learned %, longest and current streak, and a per-day tooltip.

## Engineering highlights

- **UI never blocks.** All network calls run through `mw.taskman.run_in_background`; workers use a config snapshot and never touch `mw`.
- **Resilient HTTP.** GET and PUT retry on network errors and 5xx with jittered exponential backoff; POST is never retried. Errors map to a typed hierarchy (`errors.py`: `AuthError`, `NetworkError`, `ServerError`, `ApiError`, …).
- **Validated boundaries.** Responses are parsed into typed frozen dataclasses (`from_dict`, stdlib only); settings are validated up front (https required except for localhost, username format).
- **Secrets handled carefully.** The password is never written to disk (only a rotating refresh token, in a private file), a logging filter redacts bearer tokens and credentials (and is tested), and the config holds no secrets. The add-on talks only to the AnkiHelper API, never to Supabase directly.
- **Testable design.** Logic lives in pure modules (`stats`, `heatmap`, `consent_logic`, `group_loader`, `group_state`, `groups_logic`, `account_state`, `session`, `token_store`); only the Qt modules (`ui.py`, `dialogs.py`, `groups_dialog.py`) import `aqt`, and `addon/__init__.py` skips UI setup when Anki is absent so tests run in plain Python.
- **Matches Anki's runtime.** Python is pinned to 3.13 to match Anki's bundled interpreter, and the one runtime dependency (`httpx`, pure Python) is vendored since Anki does not provide it.

## Setup

1. Copy `addon/config.example.json` to `addon/config.json` (git-ignored, so local changes never get committed). The example already points at the production backend; use `http://localhost:8000` to test against a local one. `scripts/package.sh` ignores your `config.json` and ships the example as the default config.
2. Edit `addon/config.json` if needed (options below).
3. Install the vendored dependencies (versions pinned by `uv.lock`): `uv export --no-dev --no-hashes --no-emit-project | uv pip install --target addon/vendor --python 3.13 -r -`. Everything vendored is pure Python, so it works on any OS.
4. With Anki closed, link (or copy) `addon/` into Anki's add-ons folder as `ankihelper`. To find the folder, use Tools > Add-ons > View Files.

   | OS | Add-ons folder | Link command |
   | --- | --- | --- |
   | macOS | `~/Library/Application Support/Anki2/addons21/` | `ln -s "$PWD/addon" ~/Library/Application\ Support/Anki2/addons21/ankihelper` |
   | Windows | `%APPDATA%\Anki2\addons21\` | `mklink /D "%APPDATA%\Anki2\addons21\ankihelper" "%CD%\addon"` (needs admin or Developer Mode; otherwise copy the folder) |
   | Linux | `~/.local/share/Anki2/addons21/` | `ln -s "$PWD/addon" ~/.local/share/Anki2/addons21/ankihelper` (Flatpak: `~/.var/app/net.ankiweb.Anki/data/Anki2/addons21/`) |

   Alternatively run `scripts/package.sh` and install `dist/ankihelper.ankiaddon` through Tools > Add-ons > Install from file.
5. In Anki, use Tools > AnkiHelper > Sign up (or Log in), then Test connection.

| Option | Description |
| --- | --- |
| `base_url` | Backend URL. `https` required, except for `localhost`. |
| `selected_group` | Group shown on the Groups tab; set automatically. An old `friends` list is removed on startup (with a one-time note). |
| `sharing_consent` | `false` until you accept the sharing dialog; no stats are uploaded while it is false. |
| `timeout_seconds` | HTTP timeout (default 60, long enough for a sleeping server to wake). |
| `debug_logging` | Write a redacted rotating log to `addon/user_files/ankihelper.log`. |

### Tools menu

- **Log in**: email and password dialog; keeps only the refresh token.
- **Sign up**: email, username and password dialog; calls `POST /auth/signup`.
- **Forgot password?** (button in the Log in dialog): `POST /auth/forgot-password`; shows the same message whether or not the account exists.
- **Log out**: `POST /auth/logout`, then deletes the stored token.
- **Delete account...**: shows what is lost, asks you to type your account email (looked up with `GET /auth/me`) and your password, then calls `DELETE /auth/me`. A wrong password keeps the account. On success the stored token is deleted and the Groups tab is reset.
- **Sharing**: toggles `sharing_consent`; turning it on shows the consent dialog.
- **Test connection**: calls `GET /auth/me` with the current session.
- **Push today's reviews**: `PUT /reviews/daily` (also runs automatically).
- **Re-upload review history...**: replaces the last year on the server with this computer's data, after a confirmation (typing `upload` if there are no reviews in the year). Errors are shown.
- **Groups...**: opens the Groups screen.
- **Refresh groups**: re-fetches the group list and the selected group's reviews.

### API endpoints used

| Endpoint | Used for |
| --- | --- |
| `POST /auth/signup` | Sign up (`{email, password, username}`) |
| `POST /auth/login`, `/auth/refresh`, `/auth/logout` | Session handling |
| `DELETE /auth/me` | Delete account (password in body, never retried) |
| `GET /auth/me` | Test connection |
| `PUT /reviews/daily` | Push today's running totals |
| `PUT /reviews/history`, `GET /reviews/history/status` | Past-year backfill, weekly catch-up and manual re-upload |
| `GET /groups`, `GET /groups/{id}/reviews` | Group list and the selected group's past year |
| `/groups/...` (create, join, leave, allow, ...) | Groups screen |

## Development

```sh
uv sync
uv run pytest
uv run ruff check .
uv run pyright
scripts/package.sh   # builds dist/ankihelper.ankiaddon
```

Tests use a scriptable `httpx.MockTransport` backend (no network) and a real temporary Anki `Collection` for the stats queries. They cover login/refresh/retry behaviour including concurrent 401s, request retry rules, the stats calculations, heatmap rendering, the group-loading state machine (`GroupLoader` with fake scheduling) and log redaction. The Qt modules (`ui.py`, `dialogs.py`, `groups_dialog.py`) are not unit-tested because they need a running Qt/Anki.

Packaging note: the vendored dependencies are pure Python, so a `.ankiaddon` built on one OS works on macOS, Windows and Linux.

## Project layout

```
addon/
  __init__.py        entry point; adds vendor/ to sys.path, sets up UI inside Anki
  ui.py              menus, hooks, background tasks (with the dialogs, the only code importing aqt)
  account_state.py   pure login state, last-push memory and per-account reset
  consent_logic.py   sharing-consent wording and pure checks
  dialogs.py         credentials, delete-account and empty-upload dialogs (Qt)
  api_client.py      HTTP client: retries, 401 handling, endpoint methods
  session.py         tokens, login/refresh/logout, response checking
  delete_logic.py    pure checks for the Delete account dialog
  reset_logic.py     pure checks for the Forgot password button
  token_store.py     refresh token file in user_files/ (atomic writes)
  stats.py           revlog -> daily and yearly stats
  heatmap.py         HTML/SVG/CSS/JS heatmap rendering
  group_loader.py    background group fetch, load state and retry timer (Anki bits injected)
  group_state.py     pure load/retry decisions used by the loader
  groups_logic.py    pure Groups-screen decisions
  groups_dialog.py   Groups screen (Qt)
  history_logic.py   pure history-upload decisions and messages
  history_backfill.py  automatic backfill and post-sync catch-up sequencing (Anki bits injected)
  models.py          response dataclasses (`from_dict` parsing)
  settings.py        validated config
  errors.py          exception hierarchy
  log.py             redacting rotating logger
tests/               pytest suite with fake transport and temp Collection
scripts/package.sh   builds the .ankiaddon
```

## Project status

CI (`.github/workflows/ci.yml`) runs ruff (check + format), pyright, pytest and the package smoke test. Open work is in `ROADMAP.md`; finished plans are in `docs/archive/`. The Qt modules have not been exercised by automated tests, so releases need the manual checks listed there. API reference: the backend repo's `docs/API_GUIDE.md`.

## License

AGPL-3.0-or-later. See `LICENSE`. The heatmap is based on Review Heatmap, whose Additional Terms are in `third_party/review-heatmap/LICENSE`. That file and the licenses of the vendored dependencies ship in the built add-on under `licenses/`.

Based on the Anki add-on [Review Heatmap](https://github.com/glutanimate/review-heatmap/) by Glutanimate. [Click here to support Glutanimate's work](https://glutanimate.com/support-my-work/).
