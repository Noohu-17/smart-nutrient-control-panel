# Zone controller service (T1-32)

The zones that run on the prototype rig (snm-prototype-extension-v1: a 4G gateway and its
ESP-NOW nodes) get their zone controller here. It is `sim/controller`'s zone controller, the same
rules as every simulated zone (command lifecycle, limits, Stop/Resume, the journal, 24 h
totals), on a HAL that talks to the rig instead of the farm model. The back end and the app see
the same contract messages as from any zone controller: nothing changes there.

```
 app ── back end ── broker ──── zone controller service ──── broker ──── rig gateway ── nodes
            contract 4-9 (farm/{site}/...)   sim/controller       sf schema 1 (m/<ws>/c/<channel>/sf/...)
```

## Run it

```
python -m sim.rig            # from simulator/, with the settings below
docker compose -f infra/docker-compose.dev.yml --profile rig up -d zone-controller
```

A zone on the rig is `LIVE` in `layout.json` (`source`, contract 2.4; the default on a real
farm): the service refuses a `SIMULATED` one, and the simulator never plays a live zone's
controller, so the two can't both be ZC1. With no rig yet, start the stand-in too:
`python -m sim.rig.fake --zones 1 2 --interval 10` (or `--profile fake-rig`); `--zones 1 2` plays
both rig zones, as client_B puts Zone B on rig zone 2. It publishes what gateway 1.0.0 publishes
and obeys `dose`, `switch` and `estop`; `--time-scale 10` runs doses ten times faster.
With a pack that puts no zone on the rig (client_A), the service says so once and exits cleanly,
so Compose (`restart: on-failure`) leaves it stopped.

## Config

Which zones run on the rig comes from the config pack's `io_map.json`. client_A has none;
client_B (T1-31) puts Zone A, `ZC1`, on the rig's Zone 1. `scripts/validate_config.py` and the
service check it.

```json
"controllers": {
  "ZC1": {
    "link": "SF_GATEWAY",
    "gateway": "gateway-01",
    "rigZone": 1,
    "pumps": { "N": "relay1", "P": "relay2", "K": "relay3" },
    "tdsPpmPerMsCm": 500,
    "dayTankLevelSensors": false
  }
}
```

| Key | What |
|---|---|
| `gateway`, `rigZone` | The gateway's client ID and its zone number: Zone 1 is the actuator and sensor boxes, Zone 2 the gateway's own relays and probes |
| `pumps` | Each dose line of the layout zone, to the relay that pumps it (relay1-3; relay4 is the inlet) |
| `tdsPpmPerMsCm` | The TDS meter's scale. The sensor node reports ppm = EC (µS/cm) x 0.5, so 500 ppm per mS/cm |
| `dayTankLevelSensors` | `false`: the drums have no level sensors, so the controller can't check them before a dose and a person must |

A pump's flow is its zone's `hardware.json` `pumps.zones.<zone>.doseLines.<line>.flowMlPerS`, or,
for a zone without its own rates, `pumps.doseLines.<line>.flowMlPerS`: a dose of x mL runs x / flow
seconds. Zone 2's pumps are the gateway's own and need not run at Zone 1's rate (client_B: 1 L/min
against 15 mL/min). Measure each pump three times (R-3) and put the rate there. A line's largest
dose (`limits.json` `maxPerDoseMl`) must fit in the rig's 600 s at that flow: the service and
`scripts/validate_config.py` refuse a pack where it doesn't, so a dose the review approves is
always one the rig can run for long enough. The rig's shortest dose is 0.1 s, and doses are whole
mL: on a pump faster than 10 mL/s, 1 mL is shorter than that, so the service logs, at start, the
smallest dose each such line can run, and never sends the rig a shorter one (the line delivers
nothing, so the dose ends `FAILED` or `PARTIAL`, as any line that pumped nothing).

| Setting | Default | |
|---|---|---|
| `RIG_TELEMETRY_TOPIC` | `m/dev/c/telemetry` | The gateway's telemetry topic base, `m/<workspace>/c/<channel>`, from its `secrets.h`. Never in git |
| `RIG_COMMAND_TOPIC` | `m/dev/c/relay-cmd` | Its command topic base |
| `RIG_LINK_ID` | `RIGLINK` | This service's MQTT login for the rig's topics (`MQTT_PASSWORD_RIGLINK`) |
| `RIG_OFFLINE_S` | `90` | The rig is offline after this long without a message: 3 missed heartbeats |
| `MQTT_*`, `SIM_CLIENT_DIR`, `SIM_STATE_DIR`, `LOG_LEVEL` | as the simulator | Its nvs and bootIds go in `<state dir>/rig/`, never the simulator's |

## What maps to what

| The rig (sf schema 1) | The contract |
|---|---|
| `sf/zone/1`, every 30 s and after a change | `ZONE_TELEMETRY` at the update's own `ts`, and `ZONE_STATE` when it changed |
| `water_quality.tds_ppm` / `ph` / `temp_c` | `ec` (ppm / `tdsPpmPerMsCm`), `ph`, `solTempC` |
| `tank.level_pct`, `tank.liters` | `reservoirPct`, `reservoirL` |
| a value `null`, or `tank.sensor_ok` false | `null` with `FAULT`, never a fake 0 |
| a part with `age_s` over 30, or an update over 120 s old | `STALE`: not used to dose (6.1, 14) |
| `actuators.relays` relay1-3, `tank.inlet_on` | equipment `Z<n>-DOSE-<line>` `ON`/`OFF`, `Z<n>-REFILL` `OPEN`/`CLOSED` |
| `actuators.estop` | `estop`, and the controller's E-STOP input: `ESTOP_PRESSED`, the dose `STOPPED` `ESTOP_DURING` |
| no key switch, leak sensor, circulation pump or flow, day-tank levels | `mode` `REMOTE`; `leak` false; no `Z<n>-CIRC`; those readings `null` with no quality (not fitted) |
| `DOSE` line of x mL | `sf/cmd/1` `dose`, `target` `zone:<rigZone>`, `relay`, `seconds` = x / flow, `dose_id` from the cmdId and line (the same run always gets the same id, and the rig never runs an id twice) |
| `sf/dose/1` `done` / `stopped` with `elapsed_s` | the line's `deliveredMl` = `elapsed_s` x flow: what really ran |
| `sf/cmd-result/1` not `ok` / `accepted` / `partial` / `duplicate`, or `sf/dose/1` `rejected` | the line delivered nothing (`SHORT`; the dose `FAILED` or `PARTIAL`) |
| `sf/cmd-result/1` `duplicate` | the rig already has that id (a command sent again, or re-delivered after a gateway restart): the line waits for its `sf/dose/1` |
| `STOP`, E-STOP or the key switch during a dose | `sf/cmd/1` `switch` relay OFF; `STOPPED` waits up to 30 s for the rig's report, so its mL are measured |
| `REFILL` | refused `FEATURE_OFF`: the rig fills its tank with its own auto-fill |
| `sf/heartbeat/1` | `DEVICE_STATUS` every 30 s with the gateway's firmware, revision, uptime, 4G signal and free heap |
| `sf/gateway/status` offline (its last will), or no message for `RIG_OFFLINE_S` | ZC1's link drops without a goodbye, so the broker publishes ZC1's last will: offline. Commands for it wait at the broker |
| the rig is heard again, same `boot` | ZC1 back with the same `bootId`; the controller waits 10 s for the rig's queued reports first |
| a new gateway `boot` | ZC1 starts a new boot: `restart()`, pumps off, the zone stopped, `REBOOTED`, an interrupted dose closed `REBOOT_MID_DOSE` (`PARTIAL` if anything ran, else `FAILED`) |
| ... on Zone 2 (the gateway's own relays) | they came up OFF with it and their dose won't report: the dose closes at once |
| ... on Zone 1 (the actuator box) | the box finishes a dose on time whatever the link does, so `restart()` sends `switch` relay OFF and the dose closes when the box reports what it ran (up to 30 s, as a stop). Until then the zone is busy, so a dose after an early Resume is refused `BUSY` |

Repeats (QoS 1) are dropped by (gateway, boot, schema, seq). Retained messages are read but are
not a sign of life. mL a pump reports after its line ended still count in the 24 h totals.
Commands wait in order while the broker is away (64 at most; dropping the oldest is logged), and
a publish that fails reconnects with that command still first.

## Not covered yet

- The rig's own alarms (`sf/event`: its thresholds and probe alarms) aren't forwarded. The back
  end raises its own from what ZC1 reports (T1-34): `SENSOR_FAULT` from a reading with nothing
  measured (`FAULT`), `READING_STALE` from an old part (`STALE`) or no new readings, and
  `DEVICE_OFFLINE` when the rig goes silent.
- The back end's dose review checks day-tank levels from telemetry. The rig sends none, so it
  refuses a dose from the app until the back end knows those lines have no sensors (T1-38's
  "sensors fitted" per zone).
- If the whole rig lost power (the actuator box too), its dose ended with it and won't report:
  the interrupted dose closes after 30 s with what was measured, usually nothing.
- The rig has no key switch, so the zone is always `REMOTE` and the E-stop is the only local
  interlock. Whether that's enough for the prototype is a question for the firmware team (R-4).
- Staging: the rig's broker logins and ACL come with T1-36.
- `tests/rig/recordings/` is written from the firmware's documented examples and schemas. Replace
  it with R-10's capture of the real rig when that arrives.
