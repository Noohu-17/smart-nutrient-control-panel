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

- **Online:** a device is online while its newest message is under 3 minutes old, and no last will has arrived since (§14). A clean stop sends the last will, so it shows offline at once.
- **Last seen:** the newest `ts` of any message from the device (`device.last_seen`, kept by the worker). It's the message's own `ts`, so a retained or replayed message can't make a silent device look alive.
- **Quality:** as sent, except that `OK` becomes `STALE` when the reading is over 120 s old or its controller is offline. Every other quality stays as it is (§11.2).
- **Times:** ISO-8601 UTC with milliseconds and `Z`. `simTs` is left out unless the latest reading has it.

## Where the zone object comes from

- **`readings`, `quality`, `ts`, `simTs`:** the zone's newest `ZONE_TELEMETRY` rows in the `reading` table.
- **`mode`, `estop`, `stopped`, `operation`, `latches`, `configVersion` and `equipment`:** the zone's latest `ZONE_STATE`, in `latest_message`. `equipment` is its `outputs` (§6.8 calls them outputs, §11.2 equipment), and `ZONE_STATE` is authoritative where it overlaps telemetry (§6.8).
- **`leak`, `activeCmdId`:** the zone's latest `ZONE_TELEMETRY`, in `latest_message`.

A field is `null` (or `{}` / `[]`) only while nothing has been received for it, in the form §11.2 gives a zone that has never reported. A controller's messages for another zone, from before the layout moved it, don't count.

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
