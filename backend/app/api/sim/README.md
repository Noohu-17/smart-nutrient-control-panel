# Simulator control (`app/api/sim/`)

The playground's `/api/sim` routes (contract §11.4), which the buttons on the app's Simulator screen call. Each one forwards to the simulator's own control API (T4-9) at `SIM_CONTROL_URL`, on the same path without `/api/sim`, and brings back the simulator's answer.

| Route | Forwarded to the simulator as |
| :--- | :--- |
| `GET /api/sim/state` | `GET /state` |
| `POST /api/sim/speed` `{ "x": 60 }` | `POST /speed`, the same body |
| `POST /api/sim/reset` | `POST /reset` |
| `POST /api/sim/faults` `{ "kind": "LEAK", "target": "Z1", "on": true }` | `POST /faults`, the same body (with `value` when given, for example `ZONE_MODE` `MAINT`) |
| `POST /api/sim/holds` `{ "target": "Z1", "metric": "ph", "value": 7.2, "gradual": true }` | `POST /holds`, the same body; `gradual` is `true` when left out (§11.4.2) |
| `DELETE /api/sim/holds/{target}/{metric}` | `DELETE /holds/{target}/{metric}` |
| `POST /api/sim/holds/release-all` | `POST /holds/release-all` |
| `GET /api/sim/scenario-options` | Not forwarded: the config pack's `scenarios.json`, without `v` and `_meta`, the file the simulator checks against (§11.4.1) |

## The rules

- **Playground sites only.** On a site whose `features.json` doesn't have `"playground": true` (as `GET /api/farm` reads it), every route answers `404` `NOT_FOUND` "This site has no simulator", before the body's fields are checked, and nothing is sent.
- **Only these routes.** No other path or method reaches the simulator (`POST /api/sim/scenario` and `/api/sim/scenarios` are not part of T1-20), and the simulator's own address is never in an answer.
- **The shape is checked here, the values there.** IDs, kinds and metrics must be dot-separated words (`Z1`, `POLYHOUSE`, `dayTankPct.ACID`), numbers must be JSON numbers and switches `true` or `false`; anything else is `422` `VALIDATION_ERROR` and nothing is sent. Which speeds, drills, targets and ranges are allowed comes from the config pack, so the simulator decides, and its refusal comes back to the app.
- **Only what the route built is sent.** The method, the path and the body as the models write it: fields they don't name, cookies and the app's headers are never passed on. Proxy settings in the environment are ignored, and redirects are never followed.
- **Logged.** Each call is logged on the `api.sim` logger, to stderr, with its body, the simulator's status and how long it took, for example `2026-11-05 16:02:11,532 INFO api.sim: POST /speed {"x":288} -> 200 in 4 ms`. So is a call refused because the site has no playground. An audit log entry with who made the call, and the roles (§11.4), wait for sign-in.

## Answers

| The simulator | The API answers |
| :--- | :--- |
| `2xx` | The same status and JSON (an empty answer stays empty) |
| `400`, `404`, `409`, `422` | The same status in the §11.1 shape: the simulator's `code`, `message` and `details` if it sent them that way; otherwise `BAD_REQUEST`, `NOT_FOUND`, `CONFLICT` or `VALIDATION_ERROR` with its `detail` text, or "The simulator refused the request" |
| `503` | `503` `SERVICE_UNAVAILABLE` "The simulator is not available right now" |
| Not running or unreachable | The same `503` |
| No answer within 5 s | `503` "The simulator did not answer in time, so the change may or may not have been made" (for `GET /api/sim/state`, "The simulator did not answer in time") |
| Anything else: another status, a redirect, an answer that isn't JSON or is over 1 MB | `500` `INTERNAL` "Something went wrong on the server", and the log says what came back |

`SIM_CONTROL_URL` not set, or not an `http://` or `https://` address: `503` "The simulator is not set up for this site", logged as an error. `scenarios.json` missing or not a JSON object: `500` `INTERNAL`.

## Setting

| Variable | Default | |
| :--- | :--- | :--- |
| `SIM_CONTROL_URL` | `http://127.0.0.1:8100` in dev; none on a server | The simulator's control API (T4-9), reachable only from inside the server. A path in it is kept (`http://simulator:8100/control` calls `/control/state`). |

Where it points:

- **The API on the laptop** (uvicorn) and the simulator on the laptop: the dev default, `http://127.0.0.1:8100`.
- **The dev stack's API** (`infra/docker-compose.dev.yml`): `http://simulator:8100`, the simulator started with `--profile sim`. To use a simulator on the laptop instead, start it with `SIM_CONTROL_HOST=0.0.0.0` and set `SIM_CONTROL_URL=http://host.docker.internal:8100` for the stack.
- **Staging** (`infra/docker-compose.staging.yml`): `http://simulator:8100`, on the internal network only. The simulator's port is never published.

## Try it

With the simulator's control API running, and `SIM_CONTROL_URL` set for the API:

```
curl localhost:8000/api/sim/state
curl -X POST localhost:8000/api/sim/speed -H "Content-Type: application/json" -d '{"x": 288}'
curl -X POST localhost:8000/api/sim/faults -H "Content-Type: application/json" -d '{"kind": "ESTOP", "target": "Z1", "on": true}'
curl -X POST localhost:8000/api/sim/holds -H "Content-Type: application/json" -d '{"target": "Z1", "metric": "ph", "value": 7.2}'
curl -X DELETE localhost:8000/api/sim/holds/Z1/ph
curl -X POST localhost:8000/api/sim/reset
curl localhost:8000/api/sim/scenario-options
```
