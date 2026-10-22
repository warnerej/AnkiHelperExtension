# Refresh/logout follow-ups (handoff for the next agent)

## Background
The add-on kept logging users out: `POST /auth/refresh` was rejected for refresh tokens older than the 1 h access token, and `Session._refresh_session` then deletes `user_files/session.json` (by design). Root cause is **still unconfirmed**. Findings so far (log: `~/Library/Application Support/Anki2/addons21/ankihelper/user_files/ankihelper.log`):
- Three logouts (2026-10-21 23:11, 10-22 09:47, 10-22 15:28), each "refresh rejected (Invalid or expired refresh token)"; the only refresh that worked was minutes after a login.
- The installed build was stale: it used `time.monotonic`, which stops during macOS sleep, so the client sent expired tokens and hit the 401 path. Fixed (`time.time`).
- The backend collapsed every Supabase `AuthError` into one 401, hiding the cause.

## Already done (commits `b55ff5c` add-on, `e1a5981` backend)
- Add-on: refresh sends the token currently on disk (`store.load() or self._refresh`), clears the store only on a definitive `AuthError`, logs token age ("unknown (restored from disk)" after a restart); 5xx on refresh keeps the session.
- Backend `AuthService.refresh` (`app/services/auth.py`): logs Supabase `code/status/message` (never the token); 401 only for `AuthApiError` with status < 500, else 502.

## To do
1. **Failed token save makes the disk token stale (medium).** `TokenStore.save` swallows `OSError` (`addon/token_store.py` ~49-51). If a rotation's save fails, memory holds the new token but disk holds the spent one; `_refresh_session` (`addon/session.py` ~198-201) then sends the spent token, the server rejects it, and the user is logged out (reuse may also revoke the family).
   - Fix: make `save` return a bool (or record the last token this process wrote) and have `Session` prefer `self._refresh` when the last save failed or the disk token equals one this process already spent.
   - Test (`tests/test_api_client.py`, uses `backend`/`make_client`/`store` fixtures): a store whose `save` fails after login; assert the next refresh sends the in-memory token.
2. **Rate limits still log users out (medium-low).** Backend `refresh` returns 401 for any `AuthApiError` with status < 500, including Supabase 429 (`over_request_rate_limit`) and 408 (timeout). Restrict 401 to definitive statuses (e.g. `{400, 401, 403, 404, 422}`), else 502. Add backend tests for 429 and 408 -> 502 next to `test_refresh_*` in `tests/test_auth.py`.
3. **Backend test lint.** In `tests/test_auth.py` (`_refresh_status`): combine the nested `with` (ruff SIM117) and avoid the `AuthApiError(..., "refresh_token_already_used")` literal tripping pyright's `ErrorCode` type (`cast` or `# type: ignore`). The backend baseline already has other ruff/pyright errors; don't fix those here.
4. **Docs.** `API_GUIDE.md` (error table, `502` row says only "the database call failed"): note that `/auth/refresh` returns 502 for auth-provider failures and the add-on keeps the session. Mention in both `ADDON_HANDOFF.md` files that refresh prefers the disk token.
5. **Confirm the real cause (needs the user).** After deploying the backend and installing the new add-on (`scripts/package.sh`), after the next overnight run get: the add-on log line "refresh rejected ... token age ...", and the Render log line "refresh rejected: code=...". Cross-check Supabase dashboard > Authentication > Logs and Sessions (time-box / inactivity timeout). If `code` is `refresh_token_already_used`, look for a second consumer of the token; if `session_not_found/expired`, it is a Supabase project setting and no client fix exists.

## Verify
- Add-on: `uv run pytest`, `uv run ruff check .`, `uv run pyright` (all clean at `b55ff5c`: 246 tests).
- Backend (`../AnkiHelper`): `uv run pytest` (88 pass at `e1a5981`).
- Rebuild the package afterwards (`bash scripts/package.sh`); the backend change needs a redeploy to take effect.
- Conventions: never log tokens/passwords; refresh tokens are single use; never refresh during profile close (see `CLAUDE.md`).
