**AnkiHelper settings**

- `base_url`: AnkiHelper API address, e.g. `https://api.example.com` (https required, except localhost).
- `email` / `password`: your AnkiHelper account. **The password is stored in plain text in this file** on your computer; it is never logged.
- `username`: your AnkiHelper username (3-20 letters, digits or `_`). Used by Tools > AnkiHelper: Sign up; login does not need it.
- `friends`: list of usernames whose last 30 days of reviews to fetch (unknown names are skipped).
- `timeout_seconds`: how long to wait for the server before giving up (default 15).
- `debug_logging`: write extra detail to `user_files/ankihelper.log` (secrets are always redacted).

Use Tools > AnkiHelper: Test connection to check your settings, or AnkiHelper: Sign up to create the account with the email, password and username above.
