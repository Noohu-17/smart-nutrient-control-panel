Simulates the controllers

The farm simulator plays every farm device (GW1, ZC1, ZC2, CC1, PM1) over MQTT, using the
messages and topics in `docs/contract.md`. One Python program (asyncio), one MQTT login per device.

## Run it

```
docker compose -f infra/docker-compose.dev.yml up -d mqtt     # from the repo root: the dev broker
cd simulator
python -m venv .venv
.venv\Scripts\activate                                         # Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env                                         # Mac/Linux: cp; then adjust if needed
python -m sim.main
```

Each device logs `connected` (for example `ZC1 connected to localhost:1883`). If the broker
restarts, each device retries with a growing delay (1, 2, 4 ... up to 30 s) and logs `connected`
again. Stop with Ctrl+C.

## Settings (`.env` or environment)

| Variable | Default | Meaning |
|---|---|---|
| `MQTT_HOST`, `MQTT_PORT` | `localhost`, `1883` | Broker address |
| `MQTT_PASSWORD_<DEVICE>` | unset | Password for that device's login (username = device ID). Unset = anonymous (dev broker) |
| `LOG_LEVEL` | `INFO` | Python logging level |

## Tests

```
cd simulator
pytest -q            # unit tests; broker tests run too when the dev broker is up, else they skip
pytest -q -m broker  # only the tests against a real broker
```
