**AnkiHelper settings**

- `base_url`: AnkiHelper API address, e.g. `https://api.example.com` (https required, except localhost).
- `selected_group`: id of the group shown on the Groups tab. Set automatically when you pick a group; leave it empty.
- `timeout_seconds`: how long to wait for the server before giving up (default 15).
- `debug_logging`: write extra detail to `user_files/ankihelper.log` (secrets are always redacted).

Your account is not configured here. Use Tools > AnkiHelper > Log in (or Sign up). Your password is never saved; only a refresh token is kept in `user_files/session.json`, and Log out (or Delete account) deletes it.
