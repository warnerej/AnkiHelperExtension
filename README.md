# AnkiHelperExtension

Anki add-on client for AnkiHelper.

## Setup

1. Copy `addon/config.example.json` to `addon/config.json` (this file is git-ignored, so your credentials stay local).
2. Edit `addon/config.json`: set `base_url`, `email` and `password`. Optionally set `username` (used by Sign up) and `friends`, a list of usernames whose last 30 days of reviews to fetch. `http` is only allowed for `localhost`.
3. Install the vendored dependencies: `uv pip install --target addon/vendor --python 3.13 "httpx>=0.28.1" "pydantic>=2.14.1"`.
4. With Anki closed, link the add-on: `ln -s "$PWD/addon" ~/Library/Application\ Support/Anki2/addons21/ankihelper`.
5. In Anki, use Tools > AnkiHelper: Test connection. If you have no account yet, use Tools > AnkiHelper: Sign up.

`scripts/package.sh` builds `dist/ankihelper.ankiaddon` using the example config, never your local `config.json`.

## Tools menu

- **Test connection**: logs in and calls `GET /auth/me`.
- **Sign up**: prompts for a username and calls `POST /auth/signup` with the email and password from config. Add the username to config afterwards.
- **Push today's reviews**: `PUT /reviews/daily` (also runs automatically on answers, sync and profile close).
- **Refresh friends**: `GET /profiles/{username}/reviews` for each name in `friends`; unknown usernames are skipped.

## API endpoints used

| Endpoint | Used for |
| --- | --- |
| `POST /auth/signup` | Sign up (`{email, password, username}`) |
| `POST /auth/login`, `POST /auth/refresh`, `POST /auth/logout` | Session handling |
| `GET /auth/me` | Test connection |
| `PUT /reviews/daily` | Push today's running totals |
| `GET /profiles/{username}/reviews` | A friend's past 30 days (404 if no such user) |

See `API_GUIDE.md` for request and response details.
