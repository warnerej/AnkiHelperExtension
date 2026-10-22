> **Archived 2026-10-22.** Add-on side done (Forgot password? button); backend done (migration 0009). Only the site page remains, tracked in the backend repo's `ROADMAP.md`.

# Password reset: backend and site handoff

The add-on side is done (`Session.forgot_password`, the "Forgot password?" button in the login dialog). These two pieces live in other repos.

## Backend: `POST /auth/forgot-password`
- Body `{email}`, no bearer token. Add it to the unauthenticated list.
- Calls Supabase's reset-email function (`reset_password_for_email`) with `redirect_to` set to the Kantanly reset page.
- Always returns the same `202 {"detail": "..."}` for any well-formed email, whether or not an account exists. Same body, and keep timing similar (e.g. do the Supabase call in a background task). `422` only for a malformed email. Never return 404 or 409 for this.
- Rate limit per IP and per email (hash the email). Over the limit returns `429`. The per-email limit must not reveal that the account exists: apply it whether or not the email is registered.
- Do not log the email at INFO level.

## Site page (Kantanly)
- Reads the recovery token from the link Supabase emails, asks for a new password (with confirm, 8+ characters) and sets it with Supabase `updateUser({password})`.
- Shows success and tells the user to log in again in Anki.
- Add the page URL to Supabase's allowed redirect URLs.

## Add-on behaviour after a reset
The stored refresh token stops working. The existing refresh-rejected path clears it and asks the user to log in, so there is nothing more to do in the add-on.

## Add-on messages
- 202: "If an account exists for that email, a reset link has been sent. Check your inbox and spam folder."
- 429: "Too many reset requests. Try again later."
- 422: "Enter a valid email address."
