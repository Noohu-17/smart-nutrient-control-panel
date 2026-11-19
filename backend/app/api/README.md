# Read API

The routes in `routes.py`, as listed in `docs/contract.md` §11.2. The full schema is at `/api/openapi.json`, and the pages at `/api/docs`.

| Route | Answers | From |
| :--- | :--- | :--- |
| `GET /api/farm` | `layout.json` plus `features`, without `_meta` blocks | The config pack in `CLIENT_DIR`; needs no database |
| `GET /api/zones` | `{ "items": [ … ] }`, one zone object per zone, in layout order | The database, and the config pack for `profile` and `crop` |
| `GET /api/zones/{zoneId}` | One zone object; `404` if there is no such zone | The database, and the config pack for `profile` and `crop` |
| `GET /api/climate` | The climate controller's readings and state; `200` even when it is offline | The config pack and the database |
| `GET /api/power` | The power monitor's latest `POWER_STATE`; `200` even when it is offline | The config pack and the database |
| `GET /api/devices` | `{ "items": [ … ], "nextCursor": … }`, with `?limit=` (max 200) and `?cursor=` | The database |
| `POST /api/dose-requests` | The §11.3 review of a dose: every check, `projected`, `estimate`, `canConfirm`; `201` whatever the checks say. Saved for the confirm step; nothing goes to the farm | The config pack and the database |
| `GET /api/zones/{zoneId}/dose-limits` | Per line: `maxPerDoseMl`, `max24hMl`, `dosed24hMl` (last 24 h, counted as `LIMIT_24H` counts) and `left24hMl`, for the dose form while typing (T1-37); and what the line doses, `name`, `compound`, `strengthPct`, `drum`, `purpose` (`nutrients.json`), with `pumpMlPerMin` (`hardware.json`), `null` where the pack doesn't say (T1-38) | The config pack and the database |

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
- **`profile`, `crop`:** the config pack, read on each request (`pack.read_zone_details`, T1-38). `profile` is the layout zone (growing system and units, `plantSites` added up, size, position, `tank` = its reservoir, sensors fitted, controller, dose lines, notes) with the controller's `link` from `io_map.json` (`MQTT` unless it names another); `crop` is the zone's `recipes.json` entry with the crop's name, or `null` with no recipe. A pack without `io_map.json` or `recipes.json` gives `MQTT` and `null`.

A field is `null` (or `{}` / `[]`) only while nothing has been received for it, in the form §11.2 gives a zone that has never reported. A controller's messages for another zone, from before the layout moved it, don't count.

## Where the climate and power objects come from

The devices are the layout's `climate.controller` and `power.monitor`; `online` and `lastSeen` follow the same rules as a zone's.

- **Climate `readings`, `quality`, `ts`, `simTs`:** the controller's newest `CLIMATE_TELEMETRY` rows in the `reading` table, with the zone's quality rule. `airTempByPosC` is keyed by the layout's `climate.airSensors`.
- **Climate `mode`, `manualUntil`, `stopped`, `equipment`, `latches`:** its latest `CLIMATE_STATE`, in `latest_message`.
- **Power:** its latest `POWER_STATE`, in `latest_message`, as sent. `generator` and `ups` are left out when the layout has `"generator": false` or `"ups": false`, and `null` when it has them but nothing has arrived. `quality` is `{}`, or every number `STALE` (`l1V`, `l2V`, `l3V`, and `upsBatteryPct`, `upsRuntimeMin` with a UPS) once the message is over 120 s old or the monitor is offline.

## The dose review (`dose_requests.py`, `dosing.py`)

The checks run in the §11.3 order, with the check IDs and messages of the app's fake (`app/src/mocks/dosing.ts`), against the zone's `GET /api/zones/{zoneId}` answer. Unknown means blocked (§8.3 rule 7): a state that is still `null` never passes.

| Check | Blocks when | From |
| :--- | :--- | :--- |
| `STATE_KNOWN` | no `ZONE_STATE` yet, a command in flight (`activeCmdId`), the zone stopped, or a latched fault | the zone's state |
| `DEVICES_ONLINE` | the controller is offline (3 minutes' silence, or its last will) | `device` |
| `ESTOP`, `ZONE_REMOTE` | E-STOP pressed or unknown; key switch not `REMOTE` or unknown | the zone's state |
| `NO_CONFLICT` | an `operation`, `activeCmdId`, or a dose pump or refill valve on | the zone's state |
| `LIMIT_DOSE`, `LIMIT_24H` | a line over `maxPerDoseMl`; delivered mL in the last 24 hours plus this dose over `max24hMl` | `limits.json`; `command_report`, on `simTs` on playground sites (§11.4) |
| `DAYTANK` | a day-tank holds less than its line's dose (unknown counts as empty); **warns** below `alerts.json` `DAYTANK_LOW` | readings, `alerts.json` |
| `RESERVOIR` | below `minReservoirPctToDose`, or unknown | readings, `limits.json` |
| `PROJECTED_EC_PH` | EC or pH missing, or the projection outside `ecCeiling`/`phMin`/`phMax`; **warns** outside the recipe's target, or with no recipe | `nutrients.json`, `recipes.json` |
| `FRESH_READINGS` | EC or pH older than 120 s, from an offline controller, missing, or with a quality other than `OK` (none at all counts as not `OK`) | readings |

`estimate.durationMin` is the pump time at `hardware.json` `pumps.doseLines.*.flowMlPerS` (the rates the simulator's pumps run at) plus `mixWaitS`, rounded up. Amounts that can't be a dose (a line the zone hasn't got, not whole mL, nothing above 0, a line twice) are `422` `INVALID_COMMAND`, as in the fake.

## Errors

Every error uses the §11.1 shape, including FastAPI's own, through the one handler in `backend/app/errors.py`. These routes only raise ordinary `HTTPException`s:

```json
{ "error": { "code": "NOT_FOUND", "message": "There is no zone Z9" } }
```

| Status | Code | When |
| :--- | :--- | :--- |
| 404 | `NOT_FOUND` | No such zone or path |
| 422 | `INVALID_COMMAND` | Dose amounts that can't be a dose |
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
curl http://localhost:8000/api/climate
curl http://localhost:8000/api/power
curl http://localhost:8000/api/devices
```

## Tests

`tests/test_read_api.py` and `tests/test_climate_power_api.py`, on a throwaway database with rows written by the worker's own code. From `backend/`: `.venv/Scripts/python -m pytest tests/test_read_api.py`. Without PostgreSQL, the database tests are skipped.
