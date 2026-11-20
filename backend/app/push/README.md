# Web Push for alerts (T1-41)

A raised alert reaches a phone or a computer even with the app closed, as a notification. The shapes are in the contract (§11.2, "Web Push"); this says how it works.

| File | What |
| :--- | :--- |
| `routes.py` | `GET /api/push/key`, `POST` and `DELETE /api/push/subscriptions` |
| `rules.py` | Who gets what: `alerts.json` `delivery` and `notifyRoles` |
| `endpoints.py` | Which endpoints the server will ever send to |
| `notifier.py` | The notifier service: sends what the worker queued |
| `keys.py` | `python -m app.push.keys`: a new VAPID key pair |

## How an alert becomes a notification

1. The worker raises an alert (`app/alerts.py`) and, in the same transaction, adds a row to `push_outbox`, keyed by the alert and its severity. An alert that turns `CRITICAL` later gets a second row; nothing else does.
2. The notifier, a service of its own, looks at the outbox every 2 seconds. For each row it checks the alert again: still open, not acknowledged, still that severity, and queued less than 10 minutes ago. Otherwise nothing is sent, and `outcome` says why.
3. It sends to every browser in `push_subscription` whose user is active, whose role is in that kind's `notifyRoles` (Owner, Agronomist and Operator for a kind `alerts.json` doesn't list), and that asked for that severity (`CRITICAL` unless `WARNING` was asked for too).
   - The row is first marked `SENDING` and committed, so no database transaction is open while the push services answer, and a second notifier won't take it. A notifier stopped mid-send leaves `SENDING`; that row isn't sent again.
   - The browsers are sent to at once (up to 8), so one slow push service doesn't hold up the others. Afterwards the row says `SENT` or `NO_ONE`.
4. The push service (Google, Apple, Mozilla or Microsoft) wakes the browser, and the app's service worker shows `title` and `body`.

The worker and the notifier only meet in the database. Nothing is lost when either restarts.

## Safety

- **Only the notifier reaches the internet.** On staging it is on the `internal` network, which the database, API, worker and broker share (it calls only the database), and on an `outbound` network of its own (the push services). It isn't on Caddy's network. It has no published port, and nothing reaches it. The worker, with its broker login and the farm's messages, stays without a route out.
- **Endpoints come from the browser, so they are checked.** The check runs when a subscription is saved and again before every send. An endpoint must be `https://` on port 443, on `fcm.googleapis.com`, `*.push.apple.com`, `*.push.services.mozilla.com` or `*.notify.windows.com`. It may have no user name or password, and every address its name resolves to must be public. Redirects are never followed. `http://db:5432` and an endpoint whose name points inside the network are never sent to. An endpoint that isn't a push service's is deleted. A failed lookup or a private address is tried again like no answer, and the subscription is kept, so a moment of DNS trouble doesn't remove everyone's notifications.
- **Keys.** The VAPID private key is only in the notifier's environment. The API has the public key only.
- **Logs.** A browser is logged by its push service's name and a short fingerprint, never its endpoint or keys.
- **Demo users can't subscribe.** Everyone who picks a role on the demo farm shares that demo user, so their phones would get each other's pushes.

## What the push service says back

| Answer | The notifier |
| :--- | :--- |
| 2xx | Done |
| 404, 410 | The browser has gone: its subscription is deleted |
| 429, 5xx, no answer (5 s timeout) | Tries again, 3 tries in all: waits `Retry-After` (at most 5 s), or 1 s then 2 s. So one browser takes at most 25 s, and the heartbeat is touched after every row |
| Anything else | Gives up and logs it |

A push lives 15 minutes in the push service while the device is off, then drops: a stale alarm is worse than none. `CRITICAL` pushes are sent with `Urgency: high`.

## Who stops getting pushes

- **Turning notifications off:** `DELETE /api/push/subscriptions`, for one browser.
- **Signed out everywhere:** sign-out everywhere, a new role, being turned off and an invite all delete every subscription of that user, as their sessions end too.
- **Signing out of one browser:** the server can't tell which subscription is this browser's. The app sends `DELETE` before it signs out.

## For the app (Track 2)

1. `GET /api/push/key`, then `registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: publicKey })`, from the "Notifications" setting, never on first load.
2. `POST /api/push/subscriptions` with `subscription.toJSON()` and the severities the user picked.
3. In the service worker, on `push`: `showNotification(data.title, { body: data.body, tag: data.alertId, data })`. On `notificationclick`: open the alert.
4. Before signing out: `DELETE /api/push/subscriptions { endpoint }`.

On an iPhone, Web Push works only for a site added to the Home Screen (iOS 16.4 and later), which needs a web app manifest.

## Setting it up

```
python -m app.push.keys       # once per server: VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY
```

Put both in `infra/.env.staging` and the password manager. `VAPID_SUBJECT` is a contact for the push services: `mailto:` and a team mailbox. Keep the pair: with a new one, every browser has to turn notifications on again. In dev: `--profile push` runs the notifier, with the keys in `infra/.env`.

## Not built yet

- **Repeats** (`escalationMin` in `alerts.json`): one push per alert, as T1-41 asks.
- **Removing the notification from other devices** after one acknowledges it.

## Tests

`tests/test_push.py`, with a fake push service: subscriptions, the endpoint checks, the outbox, who gets what, retries, and gone browsers.
