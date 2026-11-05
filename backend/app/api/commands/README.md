# Commands (`app/api/commands/`)

Stop, resume and following a command (contract §7.4, §7.8, §8, §11.2). The dose review is in `app/api/dose_requests.py` and `app/api/dosing.py`.

| Route | What it does | Answer |
| :--- | :--- | :--- |
| `GET /api/commands/{cmdId}` | The command as its controller last reported it: `state`, `reason`, `lines` (requested and delivered mL), `before`, `after`, `startedAt`, `endedAt`, and the `device` that reported it | `200`; `404` until the first `CMD_STATE` arrives |
| `POST /api/stop` `{ "scope": "ALL_OPERATIONS" }` or `{ "scope": "ZONE", "zone": "Z1" }` | Sends `STOP` to `farm/{site}/stop`. Never checked and never asks for confirmation (§8.3 rule 9); needs no database | `202` `{ "cmdId": "…", "state": "DISPATCHED" }` |
| `POST /api/resume` `{ "zone": "Z1" }` or `{ "scope": "CLIMATE" }` | Sends `RESUME` to `farm/{site}/zone/{zone}/cmd` or `farm/{site}/climate/cmd`, once the zone's controller (or `CC1`) is online (§8.3 rule 9) | `202`, as for stop |

`GET /api/commands/{cmdId}` reads `command_report`, which the worker fills with each command's newest `CMD_STATE`: an older report never replaces a newer one.

## What is sent

Every command uses the §7.1 envelope: `"type": "CMD"`, `"device": "CLOUD"`, a new `cmdId` and `msgId`, and `issuedAt` and `expiresAt`. A stop expires 5 minutes after `issuedAt` (§7.4); a resume after 60 s (§14). `issuedBy` is left out until people sign in.

`publish.py` sends each command as `CLOUD-api` (§3): one connection per command, QoS 1, never retained (§4). It answers only once the broker has confirmed the command (PUBACK). A command the broker hasn't confirmed is dropped with its connection, so it is never sent later. Commands go one at a time, because a second connection with the same client ID would take over the first.

## Errors

| Status | Code | When |
| :--- | :--- | :--- |
| 404 | `NOT_FOUND` | No such zone, or no report for that `cmdId` yet |
| 409 | `DEVICE_OFFLINE` | Resume: the controller is offline (3 minutes' silence, or its last will), so nothing was sent |
| 422 | `VALIDATION_ERROR` | A body that isn't a stop or a resume, for example scope `ZONE` with no zone |
| 503 | `SERVICE_UNAVAILABLE` | The broker didn't take the command; or, for resume and `GET`, the database can't answer or the farm isn't loaded yet |

Roles (§13: stop for every role, resume for Owner, Agronomist and Operator) wait for sign-in.

## Try it

With the dev stack, the worker and the simulator running:

```
curl -X POST localhost:8000/api/stop -H "Content-Type: application/json" -d '{"scope": "ZONE", "zone": "Z1"}'
curl localhost:8000/api/commands/<cmdId>
curl -X POST localhost:8000/api/resume -H "Content-Type: application/json" -d '{"zone": "Z1"}'
```
