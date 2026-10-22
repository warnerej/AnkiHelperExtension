# Changelog

All notable changes to this add-on are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

Targeting 0.1.0, the first public release.

### Added
- Daily push of review count, retention and study minutes to the AnkiHelper backend.
- GitHub-style year heatmap on the deck browser, for you and for each group.
- Log in, sign up, log out and Forgot password? dialogs; only a refresh token is stored (`user_files/session.json`), never the password.
- Groups screen: create, join with an invite code, leave, rename, transfer, manage the allowed list, delete.
- Delete account (confirm with email and password).
- Review history: automatic one-time 365-day backfill, 7-day catch-up after each sync, and a manual re-upload.
- Opt-in sharing consent (`sharing_consent`); nothing is uploaded until accepted. Tools > AnkiHelper > Sharing toggles it.
- Draft privacy policy, terms and AnkiWeb listing under `docs/`; CI and reproducible packaging (`scripts/package.sh`).

### Changed
- Sharing moved from a per-user friends list to groups; the `friends` config key is removed on startup.
- The password is no longer kept in the config; credentials from older configs are stripped.
- Default HTTP timeout raised from 15 to 60 seconds so a sleeping free-tier server can wake.
- Production URL is the default `base_url`; `manifest.json` sets `min_point_version` 260500 (Anki 26.5).

### Fixed
- Closing the profile no longer refreshes the token, which could log users out when Anki exited mid-refresh.
- Token refresh sends the token currently on disk, uses wall-clock expiry (not `monotonic`, which stops during macOS sleep), keeps the session on 5xx, and is single-flight across threads.
- Readable messages for FastAPI validation errors; group names render as plain text.
