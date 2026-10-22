# Roadmap

Open work for the add-on. Finished plans are in `docs/archive/`.

## Before the public release
- Manual test in real Anki (the Qt modules `ui.py`, `dialogs.py`, `groups_dialog.py` have no automated tests): install the built `.ankiaddon` in a fresh profile; consent dialog appears after login/signup; nothing uploads before accepting; declining stops pushes and the backfill; Tools > AnkiHelper > Sharing toggles; group names with HTML render as text; first login after a cold server succeeds within 60 s.
- History smoke test:
  - [ ] Fresh account with existing history: backfill runs after sync and the group heatmap fills in.
  - [ ] Second profile with an empty collection: backfill is skipped.
  - [ ] Manual re-upload with reviews: confirmation, upload succeeds. With no reviews: strong warning and the type-`upload` dialog.
  - [ ] Phone reviews, then sync: they appear after the catch-up.
  - [ ] Log out and in as another account in one session: that account is backfilled too.
  - [ ] Backend without the endpoints (404): nothing shown from the automatic path.
- Privacy policy and terms: replace `<SUPPORT_EMAIL>` / `<POLICY_URL>` in `docs/`, then link the policy from `CONSENT_MESSAGE` (`addon/consent_logic.py`, update its test), both READMEs and `manifest.json` `homepage`.
- AnkiWeb listing text and icon (`docs/ANKIWEB_LISTING.md`); retake screenshots if the UI changed.
- Run the GitHub Actions workflow once (it has never run on GitHub).

## Open investigation
- Unexplained logouts: the add-on logged users out when `POST /auth/refresh` was rejected. After the fixes (wall-clock expiry, sending the on-disk token, backend 502 for provider failures), check the add-on log ("refresh rejected ... token age ...") and the Render log ("refresh rejected: code=...") after an overnight run. `refresh_token_already_used` means a second consumer of the token; `session_not_found/expired` is a Supabase project setting.

## Later
- `ui.py` is large (~590 lines); extracting the account flows needs a fake `mw`.
- Send a client version header once the backend supports a compatibility check.
