**AnkiHelper settings**

- `base_url`: AnkiHelper API address, e.g. `https://api.example.com` (https required, except localhost).
- `email` / `password`: your AnkiHelper account. **The password is stored in plain text in this file** on your computer; it is never logged.
- `timeout_seconds`: how long to wait for the server before giving up (default 15).
- `debug_logging`: write extra detail to `user_files/ankihelper.log` (secrets are always redacted).

Use Tools > AnkiHelper: Test connection to check your settings.
