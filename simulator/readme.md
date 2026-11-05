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
restarts, each device retries with a growing delay (1, 2, 4 ... up to 30 s, less a random share so
the devices don't all retry at once) and logs `connected` again. Stop with Ctrl+C.

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

## Telemetry (contract 6.1-6.3)

| Message | Device | Topic | QoS, retained |
|---|---|---|---|
| `ZONE_TELEMETRY` | each zone controller | `farm/{site}/zone/{zone}/telemetry` | 0, no |
| `CLIMATE_TELEMETRY` | climate controller | `farm/{site}/climate/telemetry` | 0, no |
| `POWER_STATE` | power monitor | `farm/{site}/power/state` | 1, yes |

The clock task (`TickLoop` in `sim/clock.py`) moves the farm forward and takes one reading every 60
**simulated** seconds, on a fixed grid, but never more often than `simulator.json`
`minRealReadingIntervalS` (5 real seconds): the interval is `max(60, speed x 5)` simulated seconds.
So x1 reads once a real minute, x60 and x288 reach the cap of 12 a real minute, and while paused
there are none (heartbeats carry on). The farm still moves every tick, and a step is split at each
due time, so a reading shows the farm exactly as it was then.

A reading also goes out at once when a value crosses a limit, either way: `alerts.json` for zones
(`solTempMaxC`, `circFlowMinLpm`, `RESERVOIR_LOW`, `DAYTANK_LOW`), and `climate.json` `setpoints`
for the air. Staying beyond a limit sends nothing more, and a value flapping across one gets one
extra reading per period.

`POWER_STATE` is not on that grid: it repeats every 60 **real** seconds at any speed, even while
paused, and also goes out on every change and again after a reconnect.

Zones, equipment and dosing lines come from `layout.json`. `sim/farm.py` holds the live state: the
water comes from the farm model, and the values it has no physics for yet (air, power, circulation
flow, day-tank levels) start from `simulator.json` and stay there until a weather model, the drills
and dosing exist. Each is marked as a placeholder with its source. Nothing planted means no uptake,
until scenarios plant crops. A reading with nothing to measure is `null` with quality `FAULT`.

Everything a device sends goes through one outbox, so `seq` follows publish order across message
types. A message whose publish fails stays first in line and goes out again with the same `msgId`
and `seq`. Check real messages against Track 1's models while the simulator runs:

```
cd backend
python -m app.contract.watch --seconds 60
```

## Zone and climate state (contract 6.8, 6.9)

`ZONE_STATE` on `farm/{site}/zone/{zone}/state` and `CLIMATE_STATE` on `farm/{site}/climate/state`,
both QoS 1 and retained, so a new subscriber gets the current state at once. They go out at the
start, as soon as the state changes, and again after a reconnect. `ZONE_STATE` also goes out with
each telemetry message, as contract 6.8 says; `CLIMATE_STATE` only on change, as 6.9 says.

A change means the message body differs: mode, E-STOP, stopped, outputs, the running operation,
latches, config version, and for climate the fan stage, every piece of equipment and any chiller
setpoint. Readings and run-time counters are not state. The clock task checks every tick, even while
paused. Controllers call `publish_state_if_changed()` after each transition, so a pump that starts
and stops within one tick is still seen. State goes out before the telemetry of the same moment.

A new farm starts with `stopped: false`. Nothing changes state yet: commands arrive with the
controllers, and the tests change it directly.

## Settings (`.env` or environment)

| Variable | Default | Meaning |
|---|---|---|
| `MQTT_HOST`, `MQTT_PORT` | `localhost`, `1883` | Broker address |
| `MQTT_PASSWORD_<DEVICE>` | unset | Password for that device's login (username = device ID). Unset = anonymous (dev broker) |
| `LOG_LEVEL` | `INFO` | Python logging level |
| `SIM_SPEED` | `1` | Simulated clock speed, one of `speedOptions` |
| `SIM_CLIENT_DIR` | `../config/clients/client_A` | The client's config pack |
| `SIM_STATE_DIR` | `.state` | Where bootIds are kept between runs |

## Controller rules (contract 8.3, 9)

`sim/controller/` holds a zone controller written the way the firmware will be: standard library and
HAL only (`hal.py`), `handle()` for a command and `step()` at a fixed rate that never waits. Each
safety check is a plain function in `checks.py` that returns a reason code from contract 8.4, and
`refusal()` runs them in the contract's order. The limits are the stricter of the command's snapshot
and the controller's own (`limits.json`). It journals before switching anything, never switches
circulation, and keeps the journal, the last 50 `cmdId`s, latches and 24 h totals through a reboot.

The rules, HAL names and decisions are in `docs/controller-spec.md`. `tests/rules/` plays every file in
`tests/vectors/` against it, and `tests/vectors/TODO.md` lists the rules still to write.

## Commands (contract 7, 8)

Each zone controller runs that controller (`sim/main.py` `attach_controller`). It listens on
`farm/{site}/zone/{zone}/cmd` and `farm/{site}/stop`, and answers with `CMD_STATE` on
`.../cmd/state` and `EVENT` on `.../event`, at QoS 1, not retained. Its HAL (`sim/farm_hal.py`) is its
zone of the farm model, and its nvs is `.state/nvs_<device>.json`.

A dose runs line by line. Each pump moves stock from its day-tank into the reservoir at its rate in
`hardware.json` `pumps.doseLines` (placeholders), so EC really changes, and a flow meter counts what
it delivered. Then the mix wait runs on simulated time, and the final `CMD_STATE` carries `after`.
The clock task steps the controllers every 0.5 simulated seconds, so a pump stops within about 1 mL
of its target at any speed. Limits come from `limits.json` until `DEVICE_CONFIG` is handled.

Try one with the dev broker. `expiresAt` must still be ahead: commands live 60 seconds (contract 14).

```
docker compose -f infra/docker-compose.dev.yml exec -T mqtt mosquitto_pub -q 1 -t farm/siet-ph1/zone/Z1/cmd -m '{"v": 1, "type": "CMD", "msgId": "<uuid>", "ts": "<now>", "site": "siet-ph1", "device": "CLOUD", "cmdId": "<uuid>", "kind": "DOSE", "issuedAt": "<now>", "expiresAt": "<now + 60 s>", "issuedBy": "user:1", "target": {"zone": "Z1"}, "lines": [{"line": "A", "ml": 120}], "order": ["A"], "mixWaitS": 300, "maxDurationS": 600, "tolerancePct": 5}'
```

Times are contract time, for example `2026-11-04T09:00:00.000Z`. Watch the answers with
`mosquitto_sub -v -t "farm/+/zone/+/cmd/state"`, or with the contract watch tool, which checks
`CMD_STATE`'s shape until its model exists.

## Tests

```
cd simulator
pip install -r requirements-dev.txt   # once: test and lint tools
pytest -q            # unit tests; broker tests run too when the dev broker is up, else they skip
pytest -q -m broker  # only the tests against a real broker
```
