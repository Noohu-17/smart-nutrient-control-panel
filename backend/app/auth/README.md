# Sign-in, roles and the audit log (T1-35)

Every API call needs a signed-in user, except `GET /api/health`, `POST /api/auth/login` and `POST /api/auth/demo` (contract §11.1, §11.2). The server checks every role rule (§13); the app's locked buttons are only a hint. `X-Demo-Role` is gone: the server ignores it.

| File | What |
| :--- | :--- |
| `access.py` | `gate`, which runs before every route; the role checks (`allowed`); Stop's own check |
| `sessions.py` | The session cookie and the CSRF token, both signed with `SESSION_SECRET` |
| `passwords.py` | scrypt password hashes, from Python's own `hashlib` |
| `routes.py` | Login, demo login, two-factor, logout, logout everywhere, `GET /api/me` |
| `totp.py` | Authenticator-app codes (RFC 6238) |
| `user_admin.py` | Users and roles, for the Owner: `/api/users` |
| `audit.py` | The audit log: who did what |
| `users.py` | Server commands: `create-owner`, `reset-two-factor` |

## For the app

The contract holds these shapes ("Sign-in" and "Users" in §11.2, `RATE_LIMITED` in §11.1); this is a summary.

| Route | Body | Answer |
| :--- | :--- | :--- |
| `POST /api/auth/login` | `{ "email": "…", "password": "…" }` | `200` with the same body as `GET /api/me`, and the session cookie. `401` `UNAUTHORIZED` "Wrong email or password", the same for a wrong email and a wrong password |
| `GET /api/me` | | `{ "user": { "id", "name", "email", "role" }, "csrfToken": "…", "expiresAt": "…", "step": "DONE" }`. `role` is `OWNER`, `AGRONOMIST`, `OPERATOR` or `VIEWER`. `step` is what sign-in still needs: `DONE`, `TOTP` or `TOTP_SETUP` (below) |
| `POST /api/auth/totp` | `{ "code": "123456" }`, or `{}` | Two-factor, below |
| `POST /api/auth/demo` | `{ "role": "OPERATOR" }` | Demo mode's role picker: signs in as that role's demo user from `users.json`, answering as login does. Only on a site whose `features.json` has `demoFarm` and `playground` both `true`; `404` anywhere else. Demo users have no password |
| `POST /api/auth/logout` | | `204`: this browser is signed out |
| `POST /api/auth/logout-all` | | `204`: this user is signed out everywhere |

- **The cookie** is `sna_session`: `HttpOnly`, `Secure`, `SameSite=Lax`, path `/api`. The app never reads it; the browser sends it. With `fetch`, use `credentials: "same-origin"` (the default).
- **CSRF:** every request that isn't a `GET` sends `X-CSRF-Token` with `csrfToken` from login or `GET /api/me`. Without it, or with another session's, the answer is `403` `FORBIDDEN`.
- **A session ends** `SESSION_HOURS` after sign-in (12 by default; `expiresAt` says when), or at sign-out. After that every call answers `401`: show the sign-in page.
- **Not signed in:** `401` `UNAUTHORIZED` "Sign in first". **Wrong role:** `403` `FORBIDDEN`, with a one-line reason such as "Viewers can’t dose".
- **Too many wrong tries** (§11.1): 5 wrong passwords for one email, or 5 wrong two-factor codes for one user over every sign-in, in 15 minutes, and login or the code answers `429` `RATE_LIMITED`, the right password or code included, until the oldest of them is 15 minutes old. `Retry-After` gives the seconds to wait. The tries are counted from the audit log (`SIGN_IN_FAILED`, `TOTP_FAILED`), under a database lock per email or user, so tries sent at once can't slip past together. Anyone who knows an email can hold its sign-in up for 15 minutes this way, which is the price of the limit.

## Two-factor for the Owner (§11.1)

An Owner signs in with the password, then a 6-digit code from an authenticator app (Google Authenticator, Microsoft Authenticator, …). Other roles sign in with the password only. Demo users need no code.

1. `POST /api/auth/login` answers `step`:
   - `DONE`: signed in.
   - `TOTP`: send the code: `POST /api/auth/totp { "code": "123456" }`.
   - `TOTP_SETUP`: an Owner without two-factor yet. `POST /api/auth/totp {}` answers `{ "secret", "otpauthUri" }`: show `otpauthUri` as a QR code (or the secret to type in), then send the app's code as above.
2. A right code answers like login with `step: "DONE"` and a new session cookie. A wrong one is `400` `WRONG_CODE`; the fifth wrong one ends the session (`401`): sign in again.
3. Until `step` is `DONE`, every other route answers `403` `TWO_FACTOR_REQUIRED`, Stop included. Each code works once.

A lost phone: `python -m app.auth.users reset-two-factor --email …` on the server turns two-factor off and signs the user out; the Owner sets it up again at the next sign-in.

## Users and roles (§11.2, Owner only)

| Route | Body | Answer |
| :--- | :--- | :--- |
| `GET /api/users` | | `{ "items": [ { "id", "name", "email", "role", "active", "twoFactor", "hasPassword" } ] }` |
| `POST /api/users` | `{ "name", "email", "role" }` | `201` and the user, with no password yet. `409` if the email has an account |
| `POST /api/users/{id}/invite` | | `{ "user", "password" }`: a new random password, shown once. The Owner gives it to the person. It signs them out everywhere |
| `PATCH /api/users/{id}` | `{ "name"?, "role"?, "active"? }` | The user. A new role or `"active": false` signs them out everywhere; a turned-off user can't sign in |

The site always keeps an active Owner: turning off the last one, or giving them another role, is `409` `LAST_OWNER`. Every change is in the audit log.

## Who may do what

The roles are §13's, checked on the server:

| Route | Who |
| :--- | :--- |
| Every `GET` | Every signed-in role |
| `POST /api/stop` | **Every signed-in role, Viewer included** (§13, PRD rule 1) |
| `POST /api/dose-requests`, `POST /api/dose-requests/{id}/confirm`, `POST /api/resume` | Owner, Agronomist, Operator |
| `POST /api/alerts/{alertId}/ack` | Owner, Agronomist, Operator |
| `POST /api/sim/speed`, `/faults`, `/holds`, `/holds/release-all`, `DELETE /api/sim/holds/…` | Owner, Agronomist, Operator |
| `POST /api/sim/reset` | Owner |
| `PUT /api/stocks/{line}/npk` | Owner, Agronomist |
| `GET/POST /api/users`, `PATCH /api/users/{id}`, `POST /api/users/{id}/invite` | Owner |

## Stop works while the database is down

Stop must never wait for the database (§8.3 rule 9). So `POST /api/stop` checks only the cookie's signature, expiry and stage (two-factor done) and the CSRF token, not the session's database row. A session that was signed out less than `SESSION_HOURS` ago can therefore still Stop. That gives nothing a signed-in Viewer couldn't do, and every other route checks the database row.

## Who did what

- **Commands** carry `issuedBy: "user:<id>"` (§7.1). `sent_command.issued_by` and `dose_request.requested_by` keep the user, and an alert's `acked_by` too. `acknowledgedBy` in the alert answers is that user's name.
- **The audit log** (`audit_log`) gets a row for every sign-in, failed sign-in, wrong code, sign-out, user change, dose review, dose confirm, Stop, Resume, alert acknowledgement and simulator change, with the user, their role, the action, what it was about and its details. A Stop or a simulator change whose row the database can't take (it has already happened) is written to the `api.audit` log instead.

## Passwords and secrets

- Passwords are scrypt hashes (N=2^14, r=8, p=5, one of OWASP's settings), at least 12 characters. Only the hash is stored.
- Two-factor secrets are kept in `app_user` as the authenticator app needs them; the password is still needed with the code.
- The cookie holds a random session ID, the user ID, the expiry, the sign-in stage and an HMAC-SHA256 signature. The database keeps only the session ID's SHA-256, so a copy of `user_session` signs no one in.
- `SESSION_SECRET` signs cookies and CSRF tokens. On a server it must be set, at least 32 characters (`openssl rand -hex 32`); changing it signs everyone out. Dev uses a fixed dev secret.

## The first Owner

```
python -m app.auth.users create-owner --email owner@example.org --name "Farm Owner"     # from backend/
```

On staging, run it inside the API container (README.md, "Deploy to staging"). It needs the seed to have run, refuses when the site already has an Owner, and prints a random password once: put it in the password manager.

## Not built yet

- **A limit per network address.** The limit is per email and per user; one address trying many emails isn't limited. On staging the site login in front of the app limits who can try.
- **The app's screens** for two-factor and users (Track 2).

## Tests

`tests/test_auth.py` goes through the real sign-in. Other API tests sign in with `conftest.sign_in_as(app, role)`, which skips sessions but keeps the role checks.
