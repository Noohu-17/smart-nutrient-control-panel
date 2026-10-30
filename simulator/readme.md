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

## Devices (layout.json)

The simulator plays one device per entry in the client's `layout.json` `"devices"` list, read once
at start: GW1, ZC1, ZC2, CC1 and PM1 for client_A. Each entry needs a unique `id` (like `ZC1`) and a
`kind`: `GATEWAY`, `ZONE_CONTROLLER`, `CLIMATE_CONTROLLER` or `POWER_MONITOR`. Otherwise the
simulator refuses to start and says which entry is wrong.

Remove an entry and restart: that device no longer connects or sends heartbeats. The broker keeps
its last retained status, which says offline, because the device said so when it last stopped.

## Simulated clock (contract 11.4)

`sim/clock.py` keeps the simulated time. `SIM_SPEED` sets the speed: `0` pauses, `1` is real time,
`60` is one hour per real minute, `288` is one day in five minutes. Allowed speeds come from
`simulator.json` `speedOptions`. The simulated day starts when the simulator starts.

Every message's `ts` stays real time. On playground sites (`features.json` `"playground": true`)
messages also carry `simTs`, the simulated time. Heartbeats run on real time at any speed, even
when paused. `SimClock.tick()` gives the simulated seconds since the previous tick, the `dt` that
the farm model's step will take.

## Settings (`.env` or environment)

| Variable | Default | Meaning |
|---|---|---|
| `MQTT_HOST`, `MQTT_PORT` | `localhost`, `1883` | Broker address |
| `MQTT_PASSWORD_<DEVICE>` | unset | Password for that device's login (username = device ID). Unset = anonymous (dev broker) |
| `LOG_LEVEL` | `INFO` | Python logging level |
| `SIM_SPEED` | `1` | Simulated clock speed, one of `speedOptions` |
| `SIM_CLIENT_DIR` | `../config/clients/client_A` | The client's config pack |
| `SIM_STATE_DIR` | `.state` | Where bootIds are kept between runs |

## Tests

```
cd simulator
pip install -r requirements-dev.txt   # once: test and lint tools
pytest -q            # unit tests; broker tests run too when the dev broker is up, else they skip
pytest -q -m broker  # only the tests against a real broker
```
