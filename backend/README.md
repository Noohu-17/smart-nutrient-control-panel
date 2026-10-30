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
