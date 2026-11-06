# Commands (`app/api/commands/`)

Stop, resume, confirming a dose, and following a command (contract §7.2, §7.4, §7.8, §8, §11.2, §11.3). The dose review is in `app/api/dose_requests.py` and `app/api/dosing.py`.

| Route | What it does | Answer |
| :--- | :--- | :--- |
| `GET /api/commands/{cmdId}` | The command as its controller last reported it: `state`, `reason`, `lines` (requested and delivered mL), `before`, `after`, `startedAt`, `endedAt`, and the `device` that reported it. `DISPATCHED`, with `device` and the rest `null`, from when the broker has the command until the first `CMD_STATE` | `200`; `404` for a command nobody has sent or reported |
| `POST /api/stop` `{ "scope": "ALL_OPERATIONS" }` or `{ "scope": "ZONE", "zone": "Z1" }` | Sends `STOP` to `farm/{site}/stop`. Never checked and never asks for confirmation (§8.3 rule 9); works with the database down | `202` `{ "cmdId": "…", "state": "DISPATCHED" }` |
| `POST /api/resume` `{ "zone": "Z1" }` or `{ "scope": "CLIMATE" }` | Sends `RESUME` to `farm/{site}/zone/{zone}/cmd` or `farm/{site}/climate/cmd`, once the zone's controller (or `CC1`) is online (§8.3 rule 9) | `202`, as for stop |
| `POST /api/dose-requests/{id}/confirm`, header `Idempotency-Key` (a UUID) | Checks a reviewed dose again and sends `DOSE` to `farm/{site}/zone/{zone}/cmd` (below) | `202`, as for stop |

`GET /api/commands/{cmdId}` reads `command_report`, which the worker fills with each command's newest `CMD_STATE` (an older report never replaces a newer one), and `sent_command`, the commands this API has sent (migration 0004). The answer's shape is in contract §11.2.

## Confirming a dose (`confirm.py`)

1. **Checked again.** The review the person saw must have no `BLOCK`, and a new review of the zone as it is now must have none either, since the review may be minutes old. Otherwise the answer is `409` with the first blocking check's ID and message, as in the app's fake (for example `ESTOP`, "E-STOP pressed in Zone 1"), and nothing is sent.
2. **Saved, then sent.** The `DOSE` is saved in `sent_command`, with its dose request and `Idempotency-Key`, *before* it is sent. `dispatched_at` is set once the broker has it.
3. **Once only.** One dose request gives one command:
   - The same key again gets the first answer, and nothing is sent again (§11.1).
   - After a `503` (the broker didn't take it), the same key sends the same command again, with the same `cmdId` and expiry, until it expires; after that it is `409`. A controller never runs a `cmdId` twice (§8.3 rule 1), so even a command the broker took without saying so is dosed only once.
   - Any other confirm of the request (another key, or none) is `409`: a new dose needs a new review. A key already used for another dose request is `409` too.
   - Confirms of one dose request wait for each other, sending included (a PostgreSQL session lock), so two at once send it once.

The `DOSE` carries the request's `lines`, `order` (the lines in the layout's `doseLines` order), the `limits` snapshot the review used (§7.2), and `mixWaitS`, `maxDurationS` and `tolerancePct` from `limits.json` `zones.{zone}.dose`.

## What is sent

Every command uses the §7.1 envelope: `"type": "CMD"`, `"device": "CLOUD"`, a new `cmdId` and `msgId`, and `issuedAt` and `expiresAt`. A stop expires 5 minutes after `issuedAt` (§7.4); a resume or a dose after 60 s (§14). `issuedBy` is left out until people sign in.

`publish.py` sends each command as `CLOUD-api` (§3): one connection per command, QoS 1, never retained (§4). It answers only once the broker has confirmed the command (PUBACK). A command the broker hasn't confirmed is dropped with its connection, so it is never sent later by itself. Commands go one at a time, because a second connection with the same client ID would take over the first.

A stop or a resume is saved in `sent_command` after it is sent. If the database can't take it, the command has still gone: the API logs a warning, answers `202`, and `GET /api/commands/{cmdId}` follows it from its first `CMD_STATE`.

## Errors

| Status | Code | When |
| :--- | :--- | :--- |
| 404 | `NOT_FOUND` | No such zone or dose request, or no command with that `cmdId` |
| 409 | `DEVICE_OFFLINE` | Resume: the controller is offline (3 minutes' silence, or its last will), so nothing was sent |
| 409 | a check ID, such as `ESTOP` or `LIMIT_24H` | Confirm: a check blocks, in the review or in the new one |
| 409 | `CONFLICT` | Confirm: the dose request is already confirmed; the key is used for another dose request; or the command expired before the broker took it |
| 422 | `VALIDATION_ERROR` | A body that isn't a stop or a resume (for example scope `ZONE` with no zone), or an `Idempotency-Key` that isn't a UUID |
| 422 | `INVALID_COMMAND` | Confirm: the request's lines no longer fit the zone's dosing lines |
| 503 | `SERVICE_UNAVAILABLE` | The broker didn't take the command; or, except for stop, the database can't answer or the farm isn't loaded yet |

Roles (§13: stop for every role; resume and dose for Owner, Agronomist and Operator) wait for sign-in.

## Try it

With the dev stack (after `alembic upgrade head`), the worker and the simulator running:

```
curl -X POST localhost:8000/api/dose-requests -H "Content-Type: application/json" -d '{"zone": "Z1", "lines": [{"line": "A", "ml": 50}]}'
curl -X POST localhost:8000/api/dose-requests/<id>/confirm -H "Idempotency-Key: $(python -c 'import uuid; print(uuid.uuid4())')"
curl localhost:8000/api/commands/<cmdId>
curl -X POST localhost:8000/api/stop -H "Content-Type: application/json" -d '{"scope": "ZONE", "zone": "Z1"}'
curl -X POST localhost:8000/api/resume -H "Content-Type: application/json" -d '{"zone": "Z1"}'
```
