# Read API

The routes in `routes.py`, as listed in `docs/contract.md` §11.2. The full schema is at `/api/openapi.json`, and the pages at `/api/docs`.

| Route | Answers | From |
| :--- | :--- | :--- |
| `GET /api/farm` | `layout.json` plus `features`, without `_meta` blocks | The config pack in `CLIENT_DIR`; needs no database |
| `GET /api/zones` | `{ "items": [ … ] }`, one zone object per zone, in layout order | The database |
| `GET /api/zones/{zoneId}` | One zone object; `404` if there is no such zone | The database |
| `GET /api/devices` | `{ "items": [ … ], "nextCursor": … }`, with `?limit=` (max 200) and `?cursor=` | The database |

Every query is scoped by `org_id` and `site_id` (OPEN-11). They come from the config pack: `org_id` is the client's folder name, as in the seed.

## The rules

- **Online:** a device is online while its newest message is under 3 minutes old (§14).
- **Last seen:** the newest `ts` among a device's readings. It's the message's own `ts`, so a retained or replayed message can't make a silent device look alive.
- **Quality:** as sent, except that `OK` becomes `STALE` when the reading is over 120 s old or its controller is offline. Every other quality stays as it is (§11.2).
- **Times:** ISO-8601 UTC with milliseconds and `Z`. `simTs` is left out unless the latest reading has it.

## What isn't known yet

The zone object's readings, quality, `online`, `lastSeen`, `ts` and `simTs` come from the `reading` table. The other fields come from `ZONE_STATE` and the rest of `ZONE_TELEMETRY`, which aren't stored yet, so they say "not known", in the form §11.2 gives a zone that has never reported: `mode`, `estop`, `leak`, `stopped`, `activeCmdId`, `operation` and `configVersion` are `null`, `equipment` is `{}` and `latches` is `[]`.

A last will isn't stored either, so a device that has gone offline shows as online until its 3 minutes are up.

## Errors

Every error uses the §11.1 shape, including FastAPI's own, through the one handler in `backend/app/errors.py`. These routes only raise ordinary `HTTPException`s:

```json
{ "error": { "code": "NOT_FOUND", "message": "There is no zone Z9" } }
```

| Status | Code | When |
| :--- | :--- | :--- |
| 404 | `NOT_FOUND` | No such zone or path |
| 405 | `METHOD_NOT_ALLOWED` | Wrong method on a path |
| 422 | `VALIDATION_ERROR` | A bad query value; `details.fields` says which |
| 500 | `INTERNAL` | The config pack can't be read, or an unexpected error |
| 503 | `SERVICE_UNAVAILABLE` | The database is down or not migrated, or the seed hasn't run |

## Try it

With the dev stack running (`backend/README.md`):

```
curl http://localhost:8000/api/farm
curl http://localhost:8000/api/zones
curl http://localhost:8000/api/zones/Z1
curl http://localhost:8000/api/devices
```

## Tests

`tests/test_read_api.py`, on a throwaway database with rows written by the worker's own code. From `backend/`: `.venv/Scripts/python -m pytest tests/test_read_api.py`. Without PostgreSQL, the database tests are skipped.
