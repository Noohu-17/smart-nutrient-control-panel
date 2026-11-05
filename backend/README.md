Cloud back end: FastAPI, MQTT broker config, Postgres/TimescaleDB, Docker setup

## Contract message models

`app/contract/` has Pydantic models for the MQTT messages in `docs/contract.md`: the envelope (§5) and `ZONE_TELEMETRY`, `CLIMATE_TELEMETRY`, `POWER_STATE`, `DEVICE_STATUS` and `ZONE_STATE` (§6). `parse_farm_message(payload)` checks one decoded message and raises `pydantic.ValidationError` if it breaks the contract. Unknown fields are ignored, not rejected (contract §1).

## Checking live messages (for example from the simulator)

`app/contract/watch.py` subscribes to `farm/#` and checks every message against the models. It prints `OK`, `FAIL` with the fields that break the contract, or `SKIP` for a type with no model yet. From `backend/`, with the simulator running:

```
.venv/Scripts/python -m app.contract.watch                # until Ctrl+C
.venv/Scripts/python -m app.contract.watch --seconds 60   # stop after 60 s; exit code 1 if anything failed
```

Or inside the dev stack, once the api image is rebuilt: `docker compose -f infra/docker-compose.dev.yml exec api python -m app.contract.watch`

## The worker: storing what the farm sends

`app/worker/` connects to the broker as `CLOUD-worker` and subscribes to `farm/{site}/#`, with the site from the client's `layout.json`. It checks each message with the same models as the watch tool, then:

| Message | What it stores |
| :--- | :--- |
| `ZONE_TELEMETRY`, `CLIMATE_TELEMETRY` | One `reading` row per value, including per-line and per-position values (`dayTankPct.A`, `airTempByPosC.PAD_END`). A null stays null, and quality is stored as sent. |
| `POWER_STATE` | One `reading` row per number: `l1V`, `l2V`, `l3V`, `upsBatteryPct`, `upsRuntimeMin`. |
| `DEVICE_STATUS` | One `reading` row, `uptimeS`, so every device has a new row at least every 30 s. |
| `ZONE_STATE`, `CLIMATE_STATE`, `ZONE_TELEMETRY` | The device's newest message of that kind, one row each in `latest_message`. A message replaces it only when its (`bootId`, `seq`) is newer, so an older one never replaces a newer one. The API takes a zone's state from it. |
| A last will (`DEVICE_STATUS` with `online: false`) | `device.last_will_at`, the time it arrived. The device is offline from then until it sends anything new, which clears it. A replay doesn't. |

Each row has `ts` (real time, UTC) and `sim_ts` (simulated time, playground sites only). A repeated message is stored once: it has the same primary key, so the insert drops it.

Every message also sets its device's `device.last_seen`, the newest `ts` it has sent. A retained or replayed message can't make a silent device look alive, because its `ts` is old.

A bad message is logged and skipped, and the worker carries on. If the broker goes away it reconnects, waiting 1, 2, 4 … up to 30 s. If the database is down or not migrated yet it holds the message and retries with the same delays.

On the dev stack the worker starts with the other services. Build the database once, then start the simulator (`simulator/readme.md`). From the repo root:

```
docker compose -f infra/docker-compose.dev.yml up -d --build
docker compose -f infra/docker-compose.dev.yml exec api alembic upgrade head
docker compose -f infra/docker-compose.dev.yml exec api python -m app.db.seed
docker compose -f infra/docker-compose.dev.yml logs -f worker
```

To run it directly instead, on Windows too, stop the Compose one first, because two connections can't share the `CLOUD-worker` client ID: the broker keeps dropping one of them. From `backend/`:

```
docker compose -f ../infra/docker-compose.dev.yml stop worker
.venv/Scripts/python -m app.worker.main                       # until Ctrl+C
```

See what it stored:

```
docker compose -f infra/docker-compose.dev.yml exec db psql -U sna -d sna -c "SELECT ts, device_id, metric, value, quality FROM reading ORDER BY ts DESC LIMIT 20"
docker compose -f infra/docker-compose.dev.yml exec db psql -U sna -d sna -c "SELECT device_id, last_seen, last_will_at FROM device"
docker compose -f infra/docker-compose.dev.yml exec db psql -U sna -d sna -c "SELECT device_id, type, boot_id, seq, ts FROM latest_message"
```

To check the messages themselves, use the watch tool above: it shows `FAIL` with the fields that break the contract.

## Tests

The tests read every JSON example of these messages straight from `docs/contract.md`, so they fail when the contract and the models drift apart.

With Docker, from the repo root:

```
docker build -f backend/Dockerfile.test -t sna-backend-test .
docker run --rm sna-backend-test
```

Without Docker, from `backend/` (Python 3.13):

```
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt    # Linux/macOS: .venv/bin/python
.venv/Scripts/python -m pytest
```
