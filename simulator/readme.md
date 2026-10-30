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

## Heartbeats (contract 6.4)

Every device publishes `DEVICE_STATUS` to `farm/{site}/device/{id}/status` every 30 real seconds:
QoS 1, retained, with the full envelope (`v, type, msgId, ts, site, device, bootId, seq`). The site
comes from the client's `layout.json`. Each device's last will is
`{"v": 1, "type": "DEVICE_STATUS", "device": "<id>", "online": false}`, so if the simulator dies the
broker marks every device offline. Ctrl+C is a clean stop, which discards the will, so each device
then publishes the same offline status itself.

`bootId` goes up by 1 on every start and is kept in `simulator/.state/boot_ids.json` (not in git).
Fields that a simulated device doesn't have (`rssiDbm`, `freeHeapKb`, `fwHash`) are `null`.

Watch them without MQTT Explorer:

```
docker compose -f infra/docker-compose.dev.yml exec mqtt mosquitto_sub -v -t "farm/+/device/+/status"
```

The device list (GW1, ZC1, ZC2, CC1, PM1) is a temporary constant in `sim/main.py`; T4-4 reads it
from `layout.json`.

## Settings (`.env` or environment)

| Variable | Default | Meaning |
|---|---|---|
| `MQTT_HOST`, `MQTT_PORT` | `localhost`, `1883` | Broker address |
| `MQTT_PASSWORD_<DEVICE>` | unset | Password for that device's login (username = device ID). Unset = anonymous (dev broker) |
| `LOG_LEVEL` | `INFO` | Python logging level |
| `SIM_CLIENT_DIR` | `../config/clients/client_A` | The client's config pack |
| `SIM_STATE_DIR` | `.state` | Where bootIds are kept between runs |

## Tests

```
cd simulator
pip install -r requirements-dev.txt   # once: test and lint tools
pytest -q            # unit tests; broker tests run too when the dev broker is up, else they skip
pytest -q -m broker  # only the tests against a real broker
```
