# Smart Nutrient Advisor — Phase A Interface Contract

**Version:** v1.0 (Phase A edition) · **Lives at:** `docs/contract.md`
**Owners:** Track 1 (back end and cloud, editor) · Track 3 (simulator farm behaviour and controller rules) · Track 4 (simulator core and playground control) · Track 2 (app, sections 11–13)
**Status:** v1.0 for approval (milestone M0): approved when this version is merged with the approvals in section 16. Items still marked **[OPEN]** wait on agronomy or the vendor and block no build work; see section 15.

> **Locked: items A to F** — A: conventions (§1); B: names and IDs (§2.1–2.3); C: layout/configuration pack (§2.4–2.5); D: transport, topics and envelope (§3–5); E: device status and telemetry (§6.1–6.4); F: organization/site scoping and configuration-driven counts (OPEN-11 and OPEN-12 principles).
>
> **Settled in v1.0:** §6.6, §7–§10, §11.2, §11.4, §12 and §14; OPEN-9, OPEN-10, OPEN-15 and OPEN-16. Section 17 lists every section as changed or no change.
>
This document is the single agreement between every part of the Phase A system: the farm simulator, the back end and the app. If code and this document disagree, the code is wrong. If this document is wrong, fix it by pull request (section 16).

The app's screens and behaviour come from the **Phase 2A Build Handoff** and its design canvas. This contract follows the handoff; section 0 lists the few places where it deliberately differs.

---

## Contents

0. Scope and the Phase 2A handoff
1. Conventions
2. Identifiers and farm layout
3. Transport (MQTT)
4. Topics and permissions
5. Message envelope
6. Farm → cloud messages
7. Cloud → farm messages
8. Command lifecycle
9. Controller rules (played by the simulator)
10. Gateway rules (played by the simulator)
11. REST API
12. WebSocket (live updates to the app)
13. Roles and permissions
14. Timing constants
15. Open questions
16. Changing this contract
17. Changelog

---

## 0. Scope and the Phase 2A handoff

| Topic | Agreement |
|---|---|
| Phase | **Phase 2A**: one polyhouse at SIET Coimbatore, two zones, manual (user-started) dosing with safety checks. Scheduled and rule-based automation is not part of Phase 2A (handoff); the app shows it as disabled placeholders. |
| Phase A | The web app, back end, cloud and a **farm simulator** that plays the whole farm (gateway, controllers and equipment) as one Python program, plus a **playground** where users trigger events and see the results. There is **no hardware** in Phase A: every device in 2.2 is played by the simulator, using the topics and messages in this contract. |
| Phase A is manual | **Every action starts with a person** in the control panel: dose, refill, stop, fan stage, pad, chiller on/off and setpoint, shade, HAF fans. Nothing switches on by itself. Climate **Automatic** mode and **auto-refill** stay in the design but are **switched off** in Phase A through the site’s feature switches; the app shows them disabled with the reason “Automation: later phase”. Safety checks still block unsafe actions; they never start one. |
| No hard-coded values | Every farm-specific value (layout, equipment, limits, targets, alert thresholds, setpoint ranges, timings, feature switches, simulator settings) lives in **per-client configuration files** (2.5). Code and screens read them; nothing is fixed in code. |
| App | **One responsive web application** for desktop, tablet and phone. No separate mobile app, no installable (PWA) features, no browser push notifications in this phase. |
| Roles | **Owner, Agronomist, Operator, Viewer** (four). The team uses Owner access during build and maintenance. |
| Zone names | Zone IDs are `Z1`, `Z2` (scalable to `Z3` …). **Zone 1 = Zone A** and **Zone 2 = Zone B** in the handoff and the revamp plan. The display name comes from the layout config (2.4), so the app can label them “Zone A” and “Zone B”. |
| Layout | Benches and channels come from the layout config, not from fixed numbers. The handoff's “NFT tables” (1–12, 13–24) are the config's benches (4 per zone, 6 channels each, after the revamp). |

**Where this contract deliberately differs from the handoff:**

1. **Climate keeps an Automatic mode on the climate controller in the design**, with Manual as a timed override. In **Phase A it is switched off** (`FEATURE_OFF`) and climate is Manual only, exactly as the handoff describes.
2. **Zone mode REMOTE / LOCAL / MAINT** (the key switch on each zone panel) is an extra safety check: dosing and refill from the app work only in REMOTE.
3. **Extra dose states** the handoff's three outcomes don't cover: “Sent — waiting for the farm”, “Partial” and “Result unknown” (section 8).

Anything else found along the way is added by pull request (section 16).

---

## 1. Conventions

| Rule | Detail |
|---|---|
| Format | UTF-8 JSON. One JSON object per MQTT message. |
| Field names | `lowerCamelCase`. |
| Enum values | `UPPER_SNAKE_CASE` strings, for example `"REMOTE"`, `"ALL_OPERATIONS"`. |
| Time | ISO-8601 in **UTC** with `Z`, millisecond precision: `"2026-11-09T04:54:00.000Z"`. People see IST; machines store UTC. `ts` is always **real time**. On playground sites the simulator adds `simTs`, the simulated time (11.4). |
| IDs | Commands and messages use **UUID v4** strings. Farm things use the fixed IDs in section 2. |
| Units | Fixed per field (table below). Units are never sent in the value. |
| Missing or failed sensor | Send `null` for the value and set its quality (section 6.1). Never send a fake 0. |
| Unknown fields | Receivers **ignore** fields they don't know, so additive changes don't break anyone. |
| Version | Every message carries `"v": 1`. A breaking change bumps it (section 16). |
| Numbers | Plain JSON numbers, no strings. Dosed volumes are whole millilitres; tank volumes are litres, at the precision in the units table. |

### Units

| Quantity | Unit | Precision | Example field |
|---|---|---|---|
| Electrical conductivity | mS/cm | 0.01 | `ec` |
| pH | pH | 0.01 | `ph` |
| Temperature | °C | 0.1 | `solTempC`, `airTempC` |
| Relative humidity | % | 1 | `airRh` |
| Level | % of usable volume | 1 | `reservoirPct` |
| Reservoir volume | L | 1 | `reservoirL` |
| Day-tank volume | L | 0.1 | `dayTankL` |
| Dosed volume | mL | 1 | `ml` |
| Flow | L/min | 0.1 | `flowLpm` |
| Voltage | V | 1 | `l1V` |
| Battery | % | 1 | `upsBatteryPct` |
| UPS runtime | min | 1 | `upsRuntimeMin` |
| Dissolved oxygen | mg/L | 0.1 | `doMgL` |
| Vapour pressure deficit | kPa | 0.01 | `vpdKpa` (calculated by the cloud) |
| Duration | s | 1 | `maxDurationS` |

---

## 2. Identifiers and farm layout

### 2.1 Site, zones, benches, channels

| Thing | ID pattern | Phase 2 values |
|---|---|---|
| Site | lowercase slug | `siet-ph1` |
| Zone | `Z{n}` | `Z1` (= Zone A), `Z2` (= Zone B) |
| Bench (the handoff's “NFT table”) | `{zone}-B{n}` | `Z1-B1` … `Z1-B4`, `Z2-B1` … `Z2-B4` |
| Channel (gully) | `{bench}-C{n}` | `Z1-B1-C1` … `Z1-B1-C6`, … |
| Nursery table | `N-T{n}` | `N-T1` … `N-T6` |
| Reservoir | `R{n}` | `R1` (Z1), `R2` (Z2) |
| Dosing cabinet | `D{n}` | `D1` (Z1), `D2` (Z2) |

The layout is **read from configuration**, never hard-coded. Everything must work with 1 zone as well as 2, and with more zones, benches or channels later.

### 2.2 Devices (anything that connects to MQTT)

| Device ID | What it is | Where |
|---|---|---|
| `GW1` | Gateway: relays farm messages and holds them during an internet drop (section 10) | Electrical room |
| `ZC1` | Zone controller for Z1: sensors, dosing, refill, circulation | ZC1 panel, service strip |
| `ZC2` | Zone controller for Z2 | ZC2 panel, service strip |
| `CC1` | Climate controller: fans, pad pump, chillers, shade screen, HAF fans, air sensors | Electrical room |
| `PM1` | Power monitor: mains, generator, ATS, UPS | Electrical room |
| `SIM` | Farm simulator: plays `GW1`, `ZC1`, `ZC2`, `CC1` and `PM1`, using their device IDs and topics | Laptop or staging |
| `CLOUD` | Back end (API and worker) | Cloud server |

### 2.3 Equipment IDs

| Equipment ID | What | Owned by | Cloud may switch it? |
|---|---|---|---|
| `{zone}-DOSE-A`, `{zone}-DOSE-B`, `{zone}-DOSE-ACID` | Dosing lines (pump + flow meter) | ZC | Only through a `DOSE` command |
| `{zone}-DT-A`, `{zone}-DT-B`, `{zone}-DT-ACID` | Day-tanks (concentrate) | ZC | — (level only) |
| `{zone}-CIRC` | Circulation pump | ZC | **Never.** No command may stop it. |
| `{zone}-REFILL` | Reservoir refill valve or pump | ZC | Only through a `REFILL` command |
| `FANS` (configured IDs, e.g. `FAN1` … `FAN4` at SIET) | Exhaust fans; current SIET layout has 4, with maximum stage derived from configured fan count | CC1 | Stage 0 through the configured maximum, in climate MANUAL mode only. Turning fans to 0 also stops the pad pump. |
| `PAD1` | Cooling pad pump | CC1 | On/off in MANUAL mode only; needs fans at stage 1 or more |
| `CH1`, `CH2` | Solution chillers (C1 → Z1, C2 → Z2 on the revamp plan) | CC1 | On/off and setpoint 18–28 °C, in MANUAL mode only |
| `SHADE1` | 50 % retractable shade screen | CC1 | Open/close, in MANUAL mode only |
| `HAF1` | HAF circulation fans along the central aisle | CC1 | On/off, in MANUAL mode only |
| `FOG1` | Foggers | CC1 | **Not installed in this phase.** Reserved ID; the app shows “Not available”. If added: 2, 3 or 5 min runs, blocked when RH > 85 %. |
| `CURT1` … | Roll-up curtain motors, if motorised | CC1 | **[OPEN-3]** |

**Configuration rule:** fan identifiers and maximum stage come from `layout.json`; the current SIET layout has four fans, but neither fan count nor stage limits may be hard-coded. Zones, benches, channels, sensors, and dosing lines are likewise configuration-driven.

Dosing lines in v0 are A, B and ACID (pH down). A pH-up line is **[OPEN-4]**. Equipment that is not installed or reports offline is shown as “Not available” with disabled controls, never hidden.

### 2.4 Layout configuration (shared JSON)

The back end, app and simulator all load the same file: `config/clients/<client>/layout.json` (one folder per client, see 2.5). Example for the SIET farm:

```json
{
  "v": 1,
  "site": "siet-ph1",
  "name": "SIET Polyhouse 1",
  "timezone": "Asia/Kolkata",
  "zones": [
    {
      "id": "Z1", "name": "Zone A", "controller": "ZC1",
      "reservoir": { "id": "R1", "usableL": 1200 },
      "dosingCabinet": "D1",
      "doseLines": ["A", "B", "ACID"],
      "sensors": { "dissolvedOxygen": false },
      "dayTanks": { "A": { "usableL": 50 }, "B": { "usableL": 50 }, "ACID": { "usableL": 50 } },
      "benches": [
        { "id": "Z1-B1", "channels": 6, "lengthM": 8.5 },
        { "id": "Z1-B2", "channels": 6, "lengthM": 8.5 },
        { "id": "Z1-B3", "channels": 6, "lengthM": 8.5 },
        { "id": "Z1-B4", "channels": 6, "lengthM": 8.5 }
      ]
    },
    { "id": "Z2", "name": "Zone B", "controller": "ZC2", "reservoir": { "id": "R2", "usableL": 1200 }, "dosingCabinet": "D2", "doseLines": ["A", "B", "ACID"], "dayTanks": { "A": { "usableL": 50 }, "B": { "usableL": 50 }, "ACID": { "usableL": 50 } }, "benches": [ { "id": "Z2-B1", "channels": 6, "lengthM": 8.5 }, { "id": "Z2-B2", "channels": 6, "lengthM": 8.5 }, { "id": "Z2-B3", "channels": 6, "lengthM": 8.5 }, { "id": "Z2-B4", "channels": 6, "lengthM": 8.5 } ] }
  ],
  "nursery": { "tables": 6 },
  "climate": { "controller": "CC1", "fans": ["FAN1", "FAN2", "FAN3", "FAN4"], "padPumps": ["PAD1"], "chillers": { "Z1": "CH1", "Z2": "CH2" }, "shade": ["SHADE1"], "haf": ["HAF1"], "foggers": [], "airSensors": ["PAD_END", "MIDDLE", "FAN_END"] },
  "power": { "monitor": "PM1", "generator": true, "ups": true }
}
```

Day-tanks are 50 L each, 6 in total (handoff). Each has a level sensor. `name` is what the app shows, so the zones can be labelled “Zone A” and “Zone B” while the IDs stay `Z1` and `Z2`. `layout.json` also has a `devices` list (`id`, `kind` and, for a zone controller, `zone`) naming the devices from 2.2 that the site runs.

### 2.5 Client configuration pack

Each client (and each demo) has its own folder of configuration files. The back end validates them against JSON Schemas on start-up and loads them into the database; the simulator reads the same files. Changing a client means changing files, never code.

```
config/clients/client_A/          ← first demo pack (SIET Polyhouse 1 values)
├── layout.json        zones, benches, channels, nursery, equipment, sensors (2.4)
├── limits.json        per-dose and 24 h limits, EC ceiling, pH bounds, minimum reservoir
├── recipes.json       crops, stages, EC/pH targets, dose per run, source
├── alerts.json        alert thresholds, delays, who is told, escalation times
├── climate.json       fan stages, chiller setpoint range (e.g. 18–28 °C), manual-mode durations
├── features.json      feature switches (below)
├── users.json         demo users and roles (no real passwords in git)
├── simulator.json     drift rates, plant uptake, equipment effects, speed options, minimum real time between readings (11.4)
├── hardware.json      simulated component behaviour: sensor accuracy and response, pump rates, valve and fan delays (placeholders until parts are bought)
├── io_map.json        logical names (PUMP_A, FLOAT_HIGH …) to controller I/O channels (placeholders until the panel I/O list exists)
└── scenarios.json     “Set up your farm” presets and allowed ranges: layout options, regions, seasons,
                       weather patterns, crops and growth stages, starting water state, power and
                       internet reliability; the failure-drill catalogue (11.4.3); the values a user may
                       set and hold, with their ranges (11.4.2); the sandbox limits (11.4.1); and the “Things to try” list
```

`features.json` for Phase A:

```json
{ "playground": true, "demoFarm": true, "climateAutomatic": false, "autoRefill": false,
  "secondApproval": false, "foggers": false }
```

**New client:** collect their settings (layout, equipment, crops and targets, limits, alert contacts), save them as a new folder (`client_B/`), validate, load, and only then let them try the control panel and playground.

---

## 3. Transport (MQTT)

| Item | Agreement |
|---|---|
| Protocol | MQTT 3.1.1 (all clients). MQTT 5 features are not relied on. |
| Broker | Mosquitto on the staging server, reachable **only from inside the server** (API, worker, simulator). No MQTT port is open to the internet. |
| Client ID | The device ID (`ZC1`, `GW1`, …). The back end uses `CLOUD-api` and `CLOUD-worker`. |
| Credentials | One username and password per device; the simulator logs in as each device it plays. Never shared. |
| Sessions | Devices (simulated) and back end use persistent sessions (`cleanSession=false`). |
| Last will | Every device sets a last will on its status topic (section 6.4). |
| Payload limit | Maximum 16 KB per message. |

---

## 4. Topics and permissions

Root: `farm/{site}/…`. For Phase 2, `{site}` is `siet-ph1`.

| Topic | Publisher → subscriber | QoS | Retained | Message |
|---|---|---|---|---|
| `farm/{site}/zone/{zone}/telemetry` | ZC → cloud | 0 | No | 6.1 Zone telemetry |
| `farm/{site}/climate/telemetry` | CC1 → cloud | 0 | No | 6.2 Climate telemetry |
| `farm/{site}/power/state` | PM1 → cloud | 1 | **Yes** | 6.3 Power state |
| `farm/{site}/device/{deviceId}/status` | each device → cloud | 1 | **Yes** | 6.4 Device status (+ last will) |
| `farm/{site}/zone/{zone}/state` | ZC → cloud | 1 | **Yes** | 6.8 Zone state (on every change) |
| `farm/{site}/climate/state` | CC1 → cloud | 1 | **Yes** | 6.9 Climate state (on every change) |
| `farm/{site}/zone/{zone}/event` | ZC → cloud | 1 | No | 6.5 Event |
| `farm/{site}/climate/event` | CC1 → cloud | 1 | No | 6.5 Event |
| `farm/{site}/zone/{zone}/cmd` | cloud → ZC | 1 | **Never** | 7.2–7.3 Dose, refill · 7.8 Resume, 7.9 Clear fault (zone target) |
| `farm/{site}/climate/cmd` | cloud → CC1 | 1 | **Never** | 7.5–7.6 Climate mode, equipment · 7.8 Resume, 7.9 Clear fault (climate target) |
| `farm/{site}/stop` | cloud → ZC*, CC1 | 1 | **Never** | 7.4 Stop |
| `farm/{site}/zone/{zone}/cmd/state` | ZC → cloud | 1 | No | 6.6 Command state |
| `farm/{site}/climate/cmd/state` | CC1 → cloud | 1 | No | 6.6 Command state |
| `farm/{site}/device/{deviceId}/config` | cloud → device | 1 | **Yes** | 7.7 Device config (limits) |
| `farm/{site}/gateway/journal` | GW1 → cloud | 1 | No | 6.7 Gateway journal |

> **Commands are never retained.** A retained command would be replayed to a controller that reconnects. Config is retained on purpose: it is state, not an action.

### Permissions (broker ACL)

| Account | May publish | May subscribe |
|---|---|---|
| `ZC1` | `farm/siet-ph1/zone/Z1/#` (except `…/cmd`), `farm/siet-ph1/device/ZC1/status` | `farm/siet-ph1/zone/Z1/cmd`, `farm/siet-ph1/stop`, `farm/siet-ph1/device/ZC1/config` |
| `ZC2` | same pattern for Z2 | same pattern for Z2 |
| `CC1` | `farm/siet-ph1/climate/#` (except `…/cmd`), `farm/siet-ph1/device/CC1/status` | `farm/siet-ph1/climate/cmd`, `farm/siet-ph1/stop`, `farm/siet-ph1/device/CC1/config` |
| `PM1` | `farm/siet-ph1/power/state`, `farm/siet-ph1/device/PM1/status` | — |
| `GW1` (bridge) | everything farm devices publish, plus `farm/siet-ph1/gateway/journal` | everything the cloud publishes to the farm |
| `CLOUD-*` | `…/cmd`, `farm/siet-ph1/stop`, `…/config` | `farm/siet-ph1/#` |

---

## 5. Message envelope

Every MQTT message has these fields at the top level:

```json
{
  "v": 1,
  "type": "ZONE_TELEMETRY",
  "msgId": "0b7f2c1e-5b8e-4a59-9c4e-0c8d3a7f1a21",
  "ts": "2026-11-09T04:54:00.000Z",
  "site": "siet-ph1",
  "device": "ZC1",
  "bootId": 412,
  "seq": 18233
}
```

| Field | Meaning |
|---|---|
| `type` | Message type (sections 6–7). |
| `msgId` | UUID, unique per message. Used to drop duplicates. |
| `ts` | Real time (UTC) when the reading was taken or the event happened (not when it was sent). |
| `device` | Who sent it. Must match the MQTT client ID (the broker ACL enforces this). |
| `bootId` | Increases by 1 every time the device restarts (the simulator increases it on a simulated reboot). |
| `seq` | Increases by 1 per message within a boot. `(bootId, seq)` gives exact order and shows gaps. |
| `simTs` | Playground sites only: the simulated time when the reading was taken or the event happened. Omitted on real farms. |

Cloud → farm messages use the same envelope with `"device": "CLOUD"`.

---

## 6. Farm → cloud messages

### 6.1 Zone telemetry, `ZONE_TELEMETRY`

Sent every 60 s, and **immediately** when any value crosses an alarm threshold. On playground sites a fast clock widens the interval (11.4).

```json
{
  "v": 1, "type": "ZONE_TELEMETRY", "msgId": "…", "ts": "2026-11-09T04:54:00.000Z",
  "site": "siet-ph1", "device": "ZC1", "bootId": 412, "seq": 18233,
  "zone": "Z1",
  "readings": {
    "ec": 1.62, "ph": 6.04, "solTempC": 23.4,
    "reservoirPct": 78, "reservoirL": 936,
    "circFlowLpm": 31.0,
    "doMgL": null,
    "dayTankPct": { "A": 64, "B": 66, "ACID": 81 },
    "dayTankL": { "A": 32.0, "B": 33.0, "ACID": 40.5 }
  },
  "quality": { "ec": "OK", "ph": "OK", "solTempC": "OK", "reservoirPct": "OK", "circFlowLpm": "OK" },
  "equipment": { "Z1-CIRC": "ON", "Z1-REFILL": "CLOSED", "Z1-DOSE-A": "OFF", "Z1-DOSE-B": "OFF", "Z1-DOSE-ACID": "OFF" },
  "mode": "REMOTE",
  "estop": false,
  "leak": false,
  "activeCmdId": null
}
```

| Field | Values |
|---|---|
| `quality.*` | `OK` · `STALE` (no fresh sample) · `OUT_OF_RANGE` (outside sensor range) · `FAULT` (sensor error; value is `null`) · `UNCALIBRATED`. Physical limits (pH 0–14, percentages 0–100, EC, volumes and flows ≥ 0) apply only when quality is `OK`, `STALE` or `UNCALIBRATED`. With `OUT_OF_RANGE`, send the raw value (for example reservoir 101 % when overfilled) or `null`. With `FAULT`, the value is `null`. EC or pH that is not `OK` is never used for dosing. |
| `mode` | `REMOTE` · `LOCAL` · `MAINT`, from the key switch on the panel. The cloud never sets it. |
| `equipment` | Pumps `ON`/`OFF`; valve `OPEN`/`CLOSED`. |
| `activeCmdId` | The command the controller is running now, or `null`. |
| `doMgL` | Dissolved oxygen; only when the zone's config says the sensor is fitted, otherwise `null` and the app hides that card. |

### 6.2 Climate telemetry, `CLIMATE_TELEMETRY`

Every 60 s, immediately on alarm. On playground sites a fast clock widens the interval (11.4).

```json
{
  "v": 1, "type": "CLIMATE_TELEMETRY", "msgId": "…", "ts": "…", "site": "siet-ph1", "device": "CC1", "bootId": 77, "seq": 9120,
  "readings": { "airTempC": 31.2, "airTempByPosC": { "PAD_END": 29.4, "MIDDLE": 31.0, "FAN_END": 33.1 }, "airRh": 71, "outsideTempC": 33.8, "outsideRh": 58, "elecRoomTempC": 34.0 },
  "quality": { "airTempC": "OK", "airRh": "OK", "outsideTempC": "OK", "outsideRh": "OK", "elecRoomTempC": "OK" },
  "mode": "AUTOMATIC",
  "manualUntil": null,
  "equipment": { "fanStage": 2, "FAN1": "ON", "FAN2": "ON", "FAN3": "OFF", "FAN4": "OFF", "PAD1": "ON", "SHADE1": "OPEN", "HAF1": "ON", "CH1": { "state": "ON", "setpointC": 22.0 }, "CH2": { "state": "ON", "setpointC": 22.0 }, "FOG1": "NOT_INSTALLED" },
  "runTimeTodayMin": { "FANS": 312, "PAD1": 240 },
  "setpoints": { "airTempMaxC": 30.0, "airRhMax": 85, "solTempMaxC": 24.0 }
}
```

`mode` is `AUTOMATIC` or `MANUAL`. In `MANUAL`, `manualUntil` is the time it reverts to `AUTOMATIC` by itself. `airTempC` is the average of the three positions. VPD is calculated by the cloud from air temperature and RH, not sent by the farm. Solution temperature per zone comes from that zone's telemetry (6.1).

**Alarm limits.** “On alarm” means `airTempC` crossing `setpoints.airTempMaxC`, or `airRh` crossing `setpoints.airRhMax`, in either direction. Both limits come from `setpoints` in `climate.json`, which this message also reports. One reading goes out for the crossing, not one for as long as the value stays beyond the limit. `setpoints.solTempMaxC` is not a CC1 alarm: solution temperature is a zone reading, and its limit is the zone’s `alarms.solTempMaxC` (`alerts.json`, sent in 7.7).

### 6.3 Power state, `POWER_STATE`

Sent on every change and every 60 s. Retained.

```json
{
  "v": 1, "type": "POWER_STATE", "msgId": "…", "ts": "…", "site": "siet-ph1", "device": "PM1", "bootId": 3, "seq": 551,
  "mains": "OK",
  "phases": { "l1V": 231, "l2V": 229, "l3V": 233 },
  "source": "MAINS",
  "generator": "STOPPED",
  "ats": "MAINS",
  "ups": { "onBattery": false, "batteryPct": 100, "runtimeMin": 42 }
}
```

| Field | Values |
|---|---|
| `mains` | `OK` · `LOST` · `PHASE_FAULT` |
| `source` | `MAINS` · `GENERATOR` · `UPS_ONLY` |
| `generator` | `STOPPED` · `STARTING` · `RUNNING` · `FAULT` |
| `ats` | `MAINS` · `GENERATOR` |

The `generator` and `ups` fields are left out when the site's `layout.json` has `"generator": false` or `"ups": false`. A missing or failed phase voltage is `null`, never 0.

### 6.4 Device status, `DEVICE_STATUS` (heartbeat)

Sent every **30 s**. Retained. The last will is the same topic with `"online": false`.

```json
{
  "v": 1, "type": "DEVICE_STATUS", "msgId": "…", "ts": "…", "site": "siet-ph1", "device": "ZC1", "bootId": 412, "seq": 18234,
  "online": true,
  "fw": "1.4.2",
  "fwHash": "a1b2c3d",
  "uptimeS": 86400,
  "rssiDbm": -61,
  "clockSynced": true,
  "configVersion": 7,
  "freeHeapKb": 112
}
```

Last will payload: `{ "v": 1, "type": "DEVICE_STATUS", "device": "ZC1", "online": false }`.

### 6.5 Event, `EVENT`

Sent once when something happens. These are facts the farm sees. Alerts (who gets told, escalation) are the back end's job.

```json
{
  "v": 1, "type": "EVENT", "msgId": "…", "ts": "…", "site": "siet-ph1", "device": "ZC1", "bootId": 412, "seq": 18240,
  "zone": "Z1",
  "kind": "ESTOP_PRESSED",
  "severity": "CRITICAL",
  "message": "E-STOP pressed on the Zone A dosing cabinet",
  "systemAction": "All dosing and refill in Zone A stopped; circulation keeps running",
  "nextStep": "Check the cabinet, release the E-STOP, then press Resume in the app",
  "detail": { "source": "ZC1 panel" }
}
```

Every event answers three questions (PRD rule 7): `message` (what happened), `systemAction` (what the farm already did about it) and `nextStep` (what a person should do). The back end copies them into the alert.

| `kind` | Severity |
|---|---|
| `ESTOP_PRESSED` / `ESTOP_RELEASED` | CRITICAL / INFO |
| `MODE_CHANGED` (detail: `from`, `to`) | WARNING if leaving REMOTE, otherwise INFO |
| `LEAK_DETECTED` / `LEAK_CLEARED` | CRITICAL / INFO |
| `CIRC_NO_FLOW` / `CIRC_FLOW_RESTORED` | CRITICAL / INFO |
| `SENSOR_FAULT` / `SENSOR_RECOVERED` (detail: `metric`) | WARNING / INFO |
| `RESERVOIR_LOW` · `DAYTANK_LOW` (detail: `line`) | WARNING |
| `PUMP_FAULT` (detail: `equipment`) | CRITICAL |
| `CHILLER_FAULT` (detail: `equipment`) | CRITICAL |
| `CLIMATE_MODE_REVERTED` (manual timer ended) | INFO |
| `REBOOTED` (detail: `reason`) | WARNING |

### 6.6 Command state, `CMD_STATE`

The controller reports each step of a command it received (section 8).

```json
{
  "v": 1, "type": "CMD_STATE", "msgId": "…", "ts": "…", "site": "siet-ph1", "device": "ZC1", "bootId": 412, "seq": 18250,
  "cmdId": "6f1c2a90-3d1e-4c7b-9f52-2b8f0e6a1c44",
  "state": "COMPLETED",
  "reason": null,
  "lines": [
    { "line": "A",    "requestedMl": 120, "deliveredMl": 119, "state": "DONE" },
    { "line": "B",    "requestedMl": 120, "deliveredMl": 121, "state": "DONE" },
    { "line": "ACID", "requestedMl": 30,  "deliveredMl": 30,  "state": "DONE" }
  ],
  "before": { "ec": 1.51, "ph": 6.21 },
  "after":  { "ec": 1.63, "ph": 6.02 },
  "startedAt": "…", "endedAt": "…"
}
```

| Field | Values |
|---|---|
| `state` | `ACCEPTED` · `EXECUTING` · `COMPLETED` · `PARTIAL` · `FAILED` · `STOPPED` · `REJECTED` |
| `reason` | A reason code (section 8.4) when the state is `PARTIAL`, `FAILED`, `STOPPED` or `REJECTED`; otherwise `null`. |
| `lines` | `DOSE` only: one entry per requested line. |
| `lines[].state` | `PENDING` · `RUNNING` · `DONE` · `SHORT` (delivered below tolerance) · `SKIPPED` |
| `before` | EC and pH when the first line starts. `null` until then, and on `REJECTED`. |
| `after` | Measured after mixing, `null` until then (see “Mix wait”). |
| `startedAt`, `endedAt` | When the first output switched on and when the operation ended; `null` until they happen. |
| `detail` | Refill and climate commands only: what happened (for example `{ "fromPct": 41, "toPct": 90 }`). |

**Which state, which reason:**

| `state` | `reason` | Meaning |
|---|---|---|
| `COMPLETED` | `null` | Everything delivered within tolerance. |
| `REJECTED` | Any refusal code in 8.4 | Sent on its own, with no `ACCEPTED` before it. Nothing ran. |
| `STOPPED` | `STOP_REQUESTED`, `ESTOP_DURING`, `MODE_CHANGED_DURING`, `FAULT_DURING` | Ended early on purpose or for safety; amounts so far are reported. |
| `PARTIAL` | `FLOW_SHORT`, `NO_FLOW`, `TIMEOUT`, `REBOOT_MID_DOSE` | Some was delivered. |
| `FAILED` | `NO_FLOW`, `TIMEOUT`, `REBOOT_MID_DOSE` | Nothing was delivered. |

**Order of messages.** `ACCEPTED`, then `EXECUTING` when each line starts and ends and at least every 5 s while a line runs (progress), then the final state. Commands with no run phase (`STOP`, `RESUME`, `CLEAR_FAULT`, `CLIMATE_MODE`, `CLIMATE_EQUIPMENT`, and `REFILL` `PAUSE`, `RESUME` or `STOP`) go straight from `ACCEPTED` to `COMPLETED`, or are `REJECTED`. A command with no readable `cmdId` can’t be answered: the controller drops and logs it.

**Mix wait.** The final state goes out when the last line ends, with `after: null`. After `mixWaitS` the controller sends one more `CMD_STATE` with the same state and `after` filled in; the example above is that last message. If a STOP, E-STOP or reboot cuts the wait short, `after` stays `null` and the final state does not change. A new dose or refill during the wait is refused with `BUSY` (8.3).

**Several controllers.** Each controller reports for itself and `device` says which. A `STOP` is answered only by the controllers it applies to (8.3).

### 6.7 Gateway journal, `GATEWAY_JOURNAL`

Sent by `GW1` after its cloud link comes back, before replaying buffered messages.

```json
{
  "v": 1, "type": "GATEWAY_JOURNAL", "msgId": "…", "ts": "…", "site": "siet-ph1", "device": "GW1", "bootId": 12, "seq": 3301,
  "offlineFrom": "2026-11-09T05:02:11.000Z",
  "offlineTo":   "2026-11-09T05:19:40.000Z",
  "bufferedMessages": 1094,
  "commands": [
    { "cmdId": "6f1c…", "lastState": "PARTIAL", "reason": "REBOOT_MID_DOSE", "lastStateAt": "…" }
  ]
}
```

`commands` lists every command whose `CMD_STATE` changed while the link was down. After this, the gateway replays the buffered messages in `(device, bootId, seq)` order.

### 6.8 Zone state, `ZONE_STATE` (retained)

Sent **on every change** (and with each telemetry message), so the app sees a pump start or a stop at once instead of waiting for the next reading. Telemetry continues to include the shared equipment, mode, E-STOP/stopped, and running-operation fields so each telemetry message is a complete snapshot. Where a field appears in both telemetry and state, `ZONE_STATE` is authoritative for screen updates.

```json
{
  "v": 1, "type": "ZONE_STATE", "msgId": "…", "ts": "…", "site": "siet-ph1", "device": "ZC1", "bootId": 412, "seq": 18241,
  "zone": "Z1",
  "mode": "REMOTE",
  "estop": false,
  "stopped": false,
  "outputs": { "Z1-CIRC": "ON", "Z1-REFILL": "CLOSED", "Z1-DOSE-A": "ON", "Z1-DOSE-B": "OFF", "Z1-DOSE-ACID": "OFF" },
  "operation": { "cmdId": "6f1c…", "kind": "DOSE", "state": "EXECUTING", "line": "A" },
  "latches": [],
  "configVersion": 7
}
```

| Field | Values |
|---|---|
| `stopped` | `true` after a STOP, E-STOP, power cut or reboot, until a person sends `RESUME` (7.8). Nothing restarts by itself. |
| `operation` | The running dose or refill, or `null`. A dose stays here through its mix wait (6.6), showing its final state. |
| `latches` | Faults the controller has locked out until a person clears them, for example `CABINET_POWER_CUT` (stuck relay), `LOW_LOW`, `LEAK`. |
| `configVersion` | The limits version the controller is using (7.7), so the app can show “Approved · waiting for controller” until it matches. |

### 6.9 Climate state, `CLIMATE_STATE` (retained)

Same idea for CC1: `mode`, `manualUntil`, `stopped`, `equipment` (as in 6.2) and `latches`, sent on every change. Climate telemetry retains the shared equipment, mode, stopped/E-STOP, and running-operation fields as a complete snapshot; `CLIMATE_STATE` is authoritative for screen updates when fields overlap.

```json
{
  "v": 1, "type": "CLIMATE_STATE", "msgId": "…", "ts": "…", "site": "siet-ph1", "device": "CC1", "bootId": 77, "seq": 9121,
  "mode": "MANUAL",
  "manualUntil": "2026-11-09T05:54:00.000Z",
  "stopped": false,
  "equipment": { "fanStage": 2, "FAN1": "ON", "FAN2": "ON", "FAN3": "OFF", "FAN4": "OFF", "PAD1": "ON", "SHADE1": "OPEN", "HAF1": "ON", "CH1": { "state": "ON", "setpointC": 22.0 }, "CH2": { "state": "ON", "setpointC": 22.0 }, "FOG1": "NOT_INSTALLED" },
  "latches": []
}
```

---

## 7. Cloud → farm messages

### 7.1 Command envelope

```json
{
  "v": 1, "type": "CMD", "msgId": "…", "ts": "…", "site": "siet-ph1", "device": "CLOUD",
  "cmdId": "6f1c2a90-3d1e-4c7b-9f52-2b8f0e6a1c44",
  "kind": "DOSE",
  "issuedAt": "2026-11-09T04:55:00.000Z",
  "expiresAt": "2026-11-09T04:56:00.000Z",
  "issuedBy": "user:42",
  "target": { "zone": "Z1" }
}
```

`issuedBy` is for the audit trail only. Controllers never use it to decide anything.

### 7.2 `DOSE`

```json
{
  "…envelope": "…", "kind": "DOSE", "target": { "zone": "Z1" },
  "lines": [ { "line": "A", "ml": 120 }, { "line": "B", "ml": 120 }, { "line": "ACID", "ml": 30 } ],
  "order": ["A", "B", "ACID"],
  "mixWaitS": 300,
  "limits": { "maxPerDoseMl": { "A": 300, "B": 300, "ACID": 100 }, "max24hMl": { "A": 1500, "B": 1500, "ACID": 400 }, "ecCeiling": 2.4, "phMin": 5.5, "phMax": 6.8 },
  "maxDurationS": 600,
  "tolerancePct": 5
}
```

- Lines run one at a time, in `order`.
- `limits` is a snapshot of the cloud's limits. The controller uses the **stricter** of these and its own config (7.7). `ecCeiling`, `phMin` and `phMax` limit the cloud's projected result, which a controller can't calculate, so `EC_CEILING` and `PH_BOUNDS` are cloud-only codes (8.4).
- `mixWaitS` is how long to circulate before measuring `after`.
- A line is `SHORT` if it delivers less than `(100 − tolerancePct)%` of the request. The app shows “Mismatch” when delivered differs from requested by more than 5 %.

### 7.3 `REFILL`

```json
{ "…envelope": "…", "kind": "REFILL", "target": { "zone": "Z1" }, "action": "START", "targetPct": 90, "maxDurationS": 1800 }
```

`action`: `START` · `PAUSE` · `RESUME` · `STOP`. `maxDurationS` is the max run time (1–60 min). Auto-refill settings travel as config (7.7).

### 7.4 `STOP`

Published to `farm/{site}/stop`; every controller acts on the parts it owns.

```json
{ "…envelope": "…", "kind": "STOP", "target": { "scope": "ALL_OPERATIONS" } }
```

| `scope` | What stops | What keeps running |
|---|---|---|
| `ALL_OPERATIONS` | Dosing and refill in every zone | Circulation, cooling, fans, chillers |
| `ZONE` (`target.zone` required) | Dosing and refill in that zone | Everything else |
| `ALL_EQUIPMENT` | Dosing, refill, fans, pad pump, chillers | **Circulation** (never stopped remotely) |

A running dose ends in `STOPPED` with the amounts delivered so far. **Stopping is always allowed** (PRD rule 1): every role, Viewer included, may send any STOP, and no STOP waits for a check or a confirmation dialog. After `ALL_EQUIPMENT`, the app shows a warning that the house will heat up. What was stopped **stays stopped** (`stopped: true` in 6.8 and 6.9) until a person sends `RESUME` (7.8).

**Expiry (OPEN-10).** `ALL_OPERATIONS` and `ZONE` expire 5 minutes after `issuedAt`: a late stop of dosing or refill is still wanted, and the worst it can do is end an operation a person can start again. `ALL_EQUIPMENT` keeps the normal 60 s: fans and chillers switching off long after the request could overheat the house. “Always allowed” is about sending a stop; a controller still refuses one that has expired, with `EXPIRED_ON_ARRIVAL`. Pressing Stop again sends a new command. A controller that is already stopped answers `COMPLETED`.

### 7.5 `CLIMATE_MODE`

```json
{ "…envelope": "…", "kind": "CLIMATE_MODE", "target": {}, "mode": "MANUAL", "minutes": 30 }
```

`MANUAL` requires `minutes` (range from `climate.json`, e.g. 5–240) and reverts to `AUTOMATIC` by itself. `AUTOMATIC` ends manual mode at once. **While `climateAutomatic` is off (Phase A), climate stays in `MANUAL` with no time limit and `AUTOMATIC` is refused with `FEATURE_OFF`.**

### 7.6 `CLIMATE_EQUIPMENT`

```json
{ "…envelope": "…", "kind": "CLIMATE_EQUIPMENT", "target": { "equipment": "FANS" }, "action": "SET_STAGE", "value": 3 }
```

| `equipment` | `action` | `value` |
|---|---|---|
| `FANS` | `SET_STAGE` | 0 to the number of fans in the layout config (0–4 at SIET); 0 = off, also stops `PAD1` |
| `PAD1` | `ON` · `OFF` | — (`ON` needs fans at stage ≥ 1) |
| `CH1`, `CH2` | `ON` · `OFF` · `SET_SETPOINT` | setpoint 18–28 °C |
| `SHADE1` | `OPEN` · `CLOSE` | — |
| `HAF1` | `ON` · `OFF` | — |
| `FOG1` | `RUN` | 2, 3 or 5 minutes (only if installed; refused when RH > 85 %) |

Accepted only in `MANUAL` mode; otherwise rejected with `NOT_MANUAL`. Equipment not installed is rejected with `NOT_INSTALLED`. The app shows the new state only when `CMD_STATE` confirms it; with no confirmation within 30 s it shows “No response from device” and re-enables the button.

### 7.7 Device config, `DEVICE_CONFIG` (retained)

```json
{
  "v": 1, "type": "DEVICE_CONFIG", "msgId": "…", "ts": "…", "site": "siet-ph1", "device": "CLOUD",
  "configVersion": 7,
  "zone": "Z1",
  "limits": { "maxPerDoseMl": { "A": 300, "B": 300, "ACID": 100 }, "max24hMl": { "A": 1500, "B": 1500, "ACID": 400 }, "ecCeiling": 2.4, "phMin": 5.5, "phMax": 6.8, "minReservoirPctToDose": 30 },
  "autoRefill": { "enabled": false, "startBelowPct": 60, "stopAtPct": 90, "maxRunMin": 30 },
  "alarms": { "solTempMaxC": 26.0, "circFlowMinLpm": 15.0 }
}
```

The controller keeps the config, reports `configVersion` in its status, and **keeps using the last good config** if the cloud is unreachable. Auto-refill: `startBelowPct` < `stopAtPct`, both 5–100 %; `maxRunMin` 1–60. The per-dose limit of 300 mL per line follows the handoff; the other limit values are placeholders until the agronomy numbers arrive **[OPEN-5]**.

### 7.8 `RESUME`

```json
{ "…envelope": "…", "kind": "RESUME", "target": { "zone": "Z1" } }
```

Published to `farm/{site}/zone/{zone}/cmd`, or to `farm/{site}/climate/cmd` for climate. Ends a stop for one zone (`target.zone`) or for climate (`target: { "scope": "CLIMATE" }`) after a person has reviewed what happened (PRD rule 5: nothing resumes by itself). Interrupted doses and refills are **not** restarted; the person starts a new one if needed. Refused with `ESTOP` while the E-STOP is still pressed, and with `FAULT_LATCHED` while a latch is active.

### 7.9 `CLEAR_FAULT`

```json
{ "…envelope": "…", "kind": "CLEAR_FAULT", "target": { "zone": "Z1" }, "fault": "CABINET_POWER_CUT" }
```

Published to the same topics as `RESUME` (7.8), with the same targets: `target.zone`, or `target: { "scope": "CLIMATE" }` for a climate latch (6.9). Clears a latch (6.8) after the part has been repaired, for example the cabinet power cut after a stuck relay. Owner only. The zone or climate then still needs `RESUME`.

---

## 8. Command lifecycle

### 8.1 States

| State | Set by | Meaning | App shows |
|---|---|---|---|
| `SUBMITTED` | cloud | Request received from the app. | Spinner on the button |
| `VALIDATING` | cloud | Safety checks running. | Spinner |
| `REJECTED` | cloud **or** farm | Blocked by a check (cloud) or refused by the controller (farm). `reason` says why. | Red banner with the reason; no automatic retry |
| `AWAITING_2ND_APPROVAL` | cloud | Large dose waiting for the Owner. **Reserved: second approval is off in Phase 2A.** | — |
| `APPROVED` | cloud | Passed checks. | — |
| `DISPATCHED` | cloud | Published to MQTT; expiry clock running. | “Sent — waiting for the farm” |
| `ACCEPTED` | farm | Controller accepted it and journalled it. | “Accepted on site” |
| `EXECUTING` | farm | Running. | Progress bars per line (done, running, queued) |
| `COMPLETED` | farm | All lines delivered within tolerance. | “Completed”, requested vs delivered; step 4 “Mix and confirm” until `after` arrives, then final EC |
| `PARTIAL` | farm | Some delivered, some short or skipped. | “Partial”, with the reason and amounts |
| `FAILED` | farm | Nothing delivered. | “Device error”, with the device and its last response |
| `STOPPED` | farm | Stopped by a STOP command, E-STOP, mode change or latched fault; partial amounts recorded. | “Stopped”, with who or what stopped it (a person, the E-STOP, the key switch or a fault) and partial amounts |
| `EXPIRED` | cloud | Not accepted before `expiresAt`. | “Not accepted in time — not sent again” |
| `UNKNOWN` | cloud | Farm went silent while the command was in flight; resolved by reconciliation. | “Result unknown — farm not responding” |

Final states: `REJECTED`, `COMPLETED`, `PARTIAL`, `FAILED`, `STOPPED`, `EXPIRED`. `UNKNOWN` is not final; it resolves to one of the farm's final states or to `EXPIRED` (8.3), or stays `UNKNOWN` and is flagged for a person to check.

### 8.2 Allowed transitions

```
SUBMITTED → VALIDATING | DISPATCHED (STOP only: no checks)
VALIDATING → REJECTED | AWAITING_2ND_APPROVAL | APPROVED
AWAITING_2ND_APPROVAL → APPROVED | REJECTED (declined or timed out)
APPROVED → DISPATCHED | REJECTED (cancelled)
DISPATCHED → ACCEPTED | REJECTED (farm) | EXPIRED | UNKNOWN
ACCEPTED → EXECUTING | COMPLETED (no run phase, 6.6) | STOPPED | FAILED | UNKNOWN
EXECUTING → COMPLETED | PARTIAL | FAILED | STOPPED | UNKNOWN
UNKNOWN → COMPLETED | PARTIAL | FAILED | STOPPED | REJECTED | EXPIRED
```

Any other transition is a bug. The back end refuses it and logs it. These are not transitions:

- **A report with the same state** (progress, or `after` arriving late) updates the command and is stored.
- **A non-final report while `UNKNOWN`** (the farm is back and the dose is still running) is stored, and the state stays `UNKNOWN` until a final report arrives, so the zone stays blocked (rule 7 below).
- **A report for a command already final** (older messages replayed after `GATEWAY_JOURNAL`) is stored and changes nothing. If it shows that a command marked `EXPIRED` actually ran, the back end also flags it for a person to check.

### 8.3 Rules

1. **One command ID, one action.** The controller remembers the last 50 `cmdId`s. A repeat of a known ID gets its current state again and is **never executed twice**. This memory survives a reboot.
2. **Expiry.** The controller refuses a command that arrives at or after `expiresAt`, with `EXPIRED_ON_ARRIVAL` (reported as `REJECTED`). If no `ACCEPTED` has arrived by `expiresAt`, the cloud marks it `EXPIRED` when the controller has been heard from since dispatch, and `UNKNOWN` when it has not, because the command may have reached the farm.
3. **No automatic resend.** The cloud never re-sends an expired or unknown command. A person starts a new request.
4. **One operation per zone.** A dose or refill `START` arriving while another runs in that zone, or during a dose's mix wait, is refused with `BUSY`. `REFILL` `PAUSE`, `RESUME` and `STOP` act on the running refill and are not `BUSY`.
5. **Journal first.** The controller writes the command to its journal *before* energising any pump.
6. **No replay after reboot.** After a restart, any journalled command still `ACCEPTED` or `EXECUTING` is reported as `PARTIAL` or `FAILED` with reason `REBOOT_MID_DOSE` and the measured amounts. It is never resumed.
7. **Unknown means blocked** (PRD rule 3). If the cloud can’t tell what state a zone is in (controller offline, a command still `DISPATCHED` or `UNKNOWN`, stale readings), every new **start** command in that zone is refused with `STATE_UNKNOWN` until the state is known. Stops are never blocked.
8. **Stopped stays stopped.** While a zone is `stopped` (6.8), start commands are refused with `ZONE_STOPPED` until a person sends `RESUME`.
9. **What each command goes through:**

   | Command | Cloud checks | Farm reports |
   |---|---|---|
   | `DOSE`, `REFILL` `START` | All (11.3) | `ACCEPTED`, `EXECUTING`, final state; or `REJECTED` |
   | `REFILL` `PAUSE`, `RESUME`, `STOP` | Role and controller online | `ACCEPTED`, `COMPLETED`; or `REJECTED` |
   | `CLIMATE_MODE`, `CLIMATE_EQUIPMENT` | Role and controller online | `ACCEPTED`, `COMPLETED`; or `REJECTED` |
   | `RESUME`, `CLEAR_FAULT` | Role and controller online | `ACCEPTED`, `COMPLETED`; or `REJECTED` |
   | `STOP` | None: `SUBMITTED` goes straight to `DISPATCHED` | `ACCEPTED`, `COMPLETED`; or `REJECTED` with `EXPIRED_ON_ARRIVAL` |

10. **A STOP that reaches several controllers** has one `cmdId`. `ZONE` is answered by that zone's controller, `ALL_OPERATIONS` by every zone controller, and `ALL_EQUIPMENT` by every zone controller and `CC1`. The cloud's state is `ACCEPTED` at the first `ACCEPTED` and `COMPLETED` when all of them have reported `COMPLETED`. If any of them has not answered by `expiresAt`, it is `UNKNOWN`, and the app names the controllers that have not answered.
11. **Resolving `UNKNOWN`.** A final state in `GATEWAY_JOURNAL` (6.7) or in a `CMD_STATE` resolves it. A command that went `UNKNOWN` from `DISPATCHED` resolves to `EXPIRED` when its controller is heard from again and neither the journal, a `CMD_STATE` nor the controller's `operation` (6.8) names it: it never arrived, and it is now too late to run.

### 8.4 Reason codes

| Code | Raised by | Meaning |
|---|---|---|
| `ROLE_FORBIDDEN` | cloud | User's role may not do this. |
| `DEVICE_OFFLINE` | cloud | Controller or needed sensors not heard from recently. |
| `ESTOP` | cloud, farm | An E-STOP is pressed. |
| `NOT_REMOTE` | cloud, farm | Zone key switch is LOCAL or MAINT. |
| `BUSY` | cloud, farm | Another dose or refill is running in the zone. |
| `LIMIT_DOSE` | cloud, farm | A line exceeds its per-dose maximum. |
| `LIMIT_24H` | cloud, farm | A line's 24-hour total would be exceeded. |
| `DAYTANK_LOW` | cloud, farm | Not enough concentrate. |
| `RESERVOIR_LOW` | cloud, farm | Reservoir below the minimum to dose. |
| `EC_CEILING` | cloud | Projected EC above the ceiling. |
| `PH_BOUNDS` | cloud | Projected pH outside bounds. |
| `SENSOR_STALE` | cloud, farm | EC/pH reading too old or faulty to dose on. |
| `NEEDS_APPROVAL_DECLINED` | cloud | Owner declined, or approval timed out. |
| `CANCELLED` | cloud | The requester or the Owner cancelled before dispatch. |
| `TIMED_OUT` | cloud | No reply from the farm: a sent command has no final state, and nothing has been heard about it for 30 s since its `expiresAt`, or since its newest `CMD_STATE` if that came later. It shows as `UNKNOWN` (8.1) until the farm reports again. Not the farm's `TIMEOUT` (`maxDurationS` reached). |
| `EXPIRED_ON_ARRIVAL` | farm | Arrived after `expiresAt`. |
| `NO_FLOW` | farm | Pump ran but the flow meter saw nothing. |
| `FLOW_SHORT` | farm | Delivered below tolerance. |
| `TIMEOUT` | farm | `maxDurationS` reached. |
| `STOP_REQUESTED` | farm | A STOP command arrived. |
| `ESTOP_DURING` | farm | E-STOP pressed while running. |
| `MODE_CHANGED_DURING` | farm | Key switch left REMOTE while running. |
| `FAULT_DURING` | farm | A fault that latches (for example `LEAK`, `LOW_LOW`, `CABINET_POWER_CUT`) ended the operation. |
| `REBOOT_MID_DOSE` | farm | Controller restarted while running. |
| `NOT_MANUAL` | farm | Climate equipment command while climate is AUTOMATIC. |
| `NOT_INSTALLED` | farm | Equipment not installed (for example foggers). |
| `NEEDS_FANS` | farm | Pad pump switched on while fans are at stage 0. |
| `FEATURE_OFF` | cloud, farm | The feature is switched off for this site (for example climate Automatic or auto-refill in Phase A). |
| `CIRC_PROTECTED` | farm | Attempt to switch circulation (always refused). |
| `INVALID_COMMAND` | farm | Schema or value error. |
| `STATE_UNKNOWN` | cloud | The zone’s state isn’t known (offline controller, unconfirmed command, stale readings). |
| `ZONE_STOPPED` | cloud, farm | The zone was stopped and hasn’t been resumed. |
| `FAULT_LATCHED` | farm | A latched fault (for example `CABINET_POWER_CUT`) must be cleared first. |

---

## 9. Controller rules (played by the simulator)

In Phase A the farm simulator plays every controller (`ZC1`, `ZC2`, `CC1`, `PM1`). These rules make it behave like one, so the back end, the app and the scenario tests see realistic behaviour.

The simulated controllers are written the way the real controller firmware will be: a fixed-rate loop, explicit state machines, and access to sensors, outputs and non-volatile memory only through a small hardware abstraction layer (HAL). Their behaviour is also captured as language-neutral test vectors (JSON), so the same tests can later check the C++ firmware. Details: simulator guide, section 9.

1. Run its **own safety checks** on every command (E-STOP, REMOTE, BUSY, limits, 24 h totals from its own counters, day-tank and reservoir levels, fresh sensors) and refuse with a reason code (8.4). Never trust that the cloud checked. When several checks fail, report the first in this order: known `cmdId` (8.3 rule 1), `INVALID_COMMAND`, `EXPIRED_ON_ARRIVAL`, `ESTOP`, `FAULT_LATCHED`, `ZONE_STOPPED`, `NOT_REMOTE`, `BUSY`, `SENSOR_STALE`, `LIMIT_DOSE`, `LIMIT_24H`, `DAYTANK_LOW`, `RESERVOIR_LOW`.
2. Journal before acting; never replay after a reboot (8.3). The journal and the last 50 `cmdId`s are kept in memory and survive a simulated reboot; a “reboot mid-dose” fault ends the dose as `PARTIAL` with `REBOOT_MID_DOSE`, or `FAILED` if nothing was delivered.
3. Keep the last good config and keep working through a simulated internet drop and a simulated reboot. A malformed `DEVICE_CONFIG` is ignored, and the last good config stays in force.
4. Never switch circulation from a remote command.
5. Climate automation belongs on **CC1**; the cloud only sets the mode and manual overrides. In Phase A `climateAutomatic` is off, so CC1 acts only on manual commands and refuses `AUTOMATIC` with `FEATURE_OFF`.
6. Report every command state change with `CMD_STATE` at QoS 1, and every change of outputs, mode, stop or latches with `ZONE_STATE` / `CLIMATE_STATE` (retained).
7. `ts` is real time; on playground sites `simTs` carries the simulator’s clock, which may run fast (11.4).
8. Keep 24-hour totals per line from its own counters: a rolling 24 hours of **delivered** mL, on the simulated clock on playground sites (11.4). A line requested at 0 mL is `SKIPPED` and its pump never runs.
9. After a STOP, E-STOP, power cut or reboot, stay stopped until `RESUME`; keep latches until `CLEAR_FAULT` (7.8, 7.9). Nothing restarts by itself.
10. Every `EVENT` carries `message`, `systemAction` and `nextStep` (6.5).

---

## 10. Gateway rules (played by the simulator)

`GW1` is played by the simulator. It covers what matters to the cloud, so the back end’s offline handling is finished and tested in Phase A: a **link drop** fault stops the farm’s messages, and on recovery the simulator sends `GATEWAY_JOURNAL` and the held-back messages in order.

1. **Relay without changing anything.** Same topics, same JSON.
2. **Hold farm → cloud messages** while a simulated link drop is on: every message, unchanged, with its original `msgId`, `ts`, `simTs`, `bootId` and `seq`.
3. **Never store or delay commands.** Cloud → farm messages are delivered immediately or not at all; the controller's expiry check is the backstop.
4. **On reconnect:** send `GATEWAY_JOURNAL` first, then replay the held messages in `(device, bootId, seq)` order (6.7).
5. Publish its own `DEVICE_STATUS` with `bufferedMessages` and `cloudLink: "UP" | "DOWN"`.
6. **What the cloud sees during a drop:** silence. No last will is sent, so the cloud works out that devices are offline from the timings in section 14, as it would on a real farm. A real controller crash or disconnect still sends its last will.
7. **After a drop:** replayed messages keep their original `ts`, so they fill in the history without making a silent device look alive (`lastSeen` is the newest `ts`). The cloud resolves commands by 8.2 and 8.3.
8. **Limits in Phase A:** held messages are kept in memory and nothing is dropped. A simulator restart loses them; a real gateway's storage limits are a later-phase matter.

---

## 11. REST API

Base: `https://{host}/api`. JSON in and out. The full schema is generated by FastAPI at `/api/openapi.json`, and the app generates its types from it (`npm run gen:api`). This section is the agreed list; the OpenAPI file holds the detail.

### 11.1 Common rules

| Rule | Detail |
|---|---|
| Auth | Session cookie (`HttpOnly`, `Secure`, `SameSite=Lax`) set by `POST /auth/login`. Two-factor (authenticator app) for the Owner. |
| CSRF | Every non-GET request sends header `X-CSRF-Token` (value from `GET /me`). |
| Idempotency | `POST` endpoints that create commands accept header `Idempotency-Key` (UUID). A repeat returns the first result. |
| Errors | `{ "error": { "code": "LIMIT_DOSE", "message": "Line A exceeds 300 mL per dose", "details": {…} } }`. `code` uses section 8.4 where relevant. |
| HTTP codes | 200 OK · 201 created · 202 accepted (command started) · 400 bad input · 401 not signed in · 403 role · 404 · 409 conflict (BUSY, state) · 422 validation · 429 rate limit · 503 service unavailable (health check: database or broker down) |
| Lists | `?limit=` (max 200) and `?cursor=`; response `{ "items": […], "nextCursor": "…" }`. |
| Time ranges | `?from=` and `?to=` in ISO-8601, or `?range=24h|7d|30d`. |

**General error codes.** An error with no reason code of its own in 8.4 uses one of these:

| Status | `code` | When |
|---|---|---|
| 404 | `NOT_FOUND` | No such zone or path. |
| 405 | `METHOD_NOT_ALLOWED` | The path doesn't take this method. The `Allow` header says which it does. |
| 422 | `VALIDATION_ERROR` | A value in the request isn't valid. `details.fields` lists each one, for example `{ "field": "query.limit", "message": "…" }`. |
| 500 | `INTERNAL` | An unexpected error on the server. The message gives no detail. |
| 503 | `SERVICE_UNAVAILABLE` | The database can't answer, or the farm hasn't been loaded into it yet. |

`GET /health` is the one exception: its `503` has its own body, with `"status": "DEGRADED"`.

### 11.2 Endpoints

| Method and path | Purpose | Roles that may call it |
|---|---|---|
| `POST /auth/login` · `POST /auth/totp` · `POST /auth/logout` · `POST /auth/logout-all` | Sign in (with two-factor), sign out | Everyone |
| `GET /me` | Current user, role, CSRF token | Everyone signed in |
| `GET /farm` | Layout + features (below) | All |
| `GET /zones` · `GET /zones/{zoneId}` | Latest readings and state of every zone, or of one (below) | All |
| `GET /zones/{zoneId}/readings?metric=ec&range=24h` | Chart data (auto-bucketed) | All |
| `GET /zones/{zoneId}/operations` | Recent doses and refills | All |
| `POST /dose-estimates` `{ zone, lines }` | Estimated duration and projected EC while typing (called 300 ms after the last keystroke; no side effects) | Owner, Agronomist, Operator |
| `POST /dose-requests` | Run the checks; returns check results (11.3) | Owner, Agronomist, Operator |
| `POST /dose-requests/{id}/confirm` | Create and dispatch the command | Owner, Agronomist, Operator |
| `POST /dose-requests/{id}/approve` · `/decline` | Second approval (reserved; off in Phase 2A) | Owner |
| `GET /commands/{cmdId}` | The command's latest state and lines (below) | All |
| `POST /commands/{cmdId}/cancel` | Cancel before dispatch | Requester, Owner |
| `POST /zones/{zoneId}/refill` `{ "action": "START", "targetPct": 90 }` | Refill control | Owner, Agronomist, Operator |
| `PATCH /zones/{zoneId}/auto-refill` | Auto-refill settings | Owner, Agronomist |
| `POST /stop` `{ "scope": "ALL_OPERATIONS" }` | Stop (7.4); never checked, never asks for confirmation | **All roles, Viewer included** (handoff, PRD rule 1) |
| `POST /resume` `{ "zone": "Z1" }` or `{ "scope": "CLIMATE" }` | Resume after a stop, once reviewed (7.8) | Owner, Agronomist, Operator |
| `POST /zones/{zoneId}/clear-fault` · `POST /climate/clear-fault` `{ "fault": "CABINET_POWER_CUT" }` | Clear a latched fault after repair (7.9) | Owner |
| `GET /climate` | Latest readings and state of the climate controller (below) | All |
| `POST /climate/mode` `{ "mode": "MANUAL", "minutes": 30 }` | Climate mode | Owner, Agronomist, Operator |
| `POST /climate/equipment/{equipmentId}` `{ "action": "SET_STAGE", "value": 3 }` | Manual equipment control (7.6) | Owner, Agronomist, Operator |
| `GET /recipes` · `POST /recipes` · `POST /recipes/{id}/versions` · `POST /zones/{zoneId}/recipe` | Recipes | Read: all · write: Owner, Agronomist |
| `GET /limits` · `POST /limit-proposals` (reason required) · `POST /limit-proposals/{id}/decision` `{ "decision": "APPROVE" \| "REJECT" \| "REQUEST_CHANGES", "comment" }` | Safety limits; one pending proposal per limit per zone | Propose: Owner, Agronomist · decide: Owner |
| `GET /benches/{benchId}/cycles` · `POST /benches/{benchId}/cycles` · `PATCH /cycles/{id}` | Crop cycles | All except Viewer |
| `GET /inventory` · `POST /inventory/refills` | Day-tanks, use, run-out; record a hand refill | Read: all · record: all except Viewer |
| `GET /alerts?status=OPEN&severity=CRITICAL` · `POST /alerts/{id}/ack` | Alerts | Ack: all except Viewer |
| `GET /operations` · `GET /operations.csv` | Operation history and export | Read: all · export: all except Viewer |
| `GET /devices` · `GET /devices/{deviceId}` · `GET /devices/{deviceId}/diagnostics` | Devices | Diagnostics: Owner |
| `GET /power` | Latest power state (below) | All |
| `GET /users` · `POST /users` · `PATCH /users/{id}` · `POST /users/{id}/invite` | Users and roles | Owner |
| `GET /health` | Uptime check | Public (no data) |

**`GET /farm`** returns the site's `layout.json` (2.4) with one more field, `features`, the contents of its `features.json` (2.5). It is everything the app needs to draw zones, equipment and disabled features, so screens fix nothing in code. `_meta` blocks are never sent. On a playground sandbox it is the layout that user set up (11.4.1).

**`GET /zones/{zoneId}`** always answers `200` for a zone in the layout, including when its controller is offline; `404` means no such zone. An offline zone returns its last values with `"online": false`, never an error, so the app can show them marked as old. `GET /zones` returns the same object for every zone, as `{ "items": […] }`.

```json
{
  "zone": "Z2",
  "online": false,
  "lastSeen": "2026-11-09T05:02:11.000Z",
  "ts": "2026-11-09T05:01:30.000Z",
  "readings": {
    "ec": 1.58, "ph": 6.10, "solTempC": 23.9,
    "reservoirPct": 64, "reservoirL": 768,
    "circFlowLpm": 30.5,
    "doMgL": null,
    "dayTankPct": { "A": 18, "B": 55, "ACID": 72 },
    "dayTankL": { "A": 9.0, "B": 27.5, "ACID": 36.0 }
  },
  "quality": { "ec": "STALE", "ph": "STALE", "solTempC": "STALE", "reservoirPct": "STALE", "circFlowLpm": "STALE" },
  "equipment": { "Z2-CIRC": "ON", "Z2-REFILL": "CLOSED", "Z2-DOSE-A": "OFF", "Z2-DOSE-B": "OFF", "Z2-DOSE-ACID": "OFF" },
  "mode": "REMOTE",
  "estop": false,
  "leak": false,
  "stopped": false,
  "activeCmdId": null,
  "operation": null,
  "latches": [],
  "configVersion": 7
}
```

| Field | Rule |
|---|---|
| `online` | `false` once the zone's controller is **offline** (section 14: 3 minutes without any message, or its last will). Same meaning as `online` in the `device_state` WebSocket message (section 12). |
| `lastSeen` | The newest `ts` of any message from the controller. `null` if it has never been heard from. |
| `ts`, `simTs` | When the latest reading was taken. `simTs` on playground sites only. |
| `readings` | The latest values from 6.1, as sent, in the units of section 1. |
| `quality` | As sent, except that a value older than the fresh limit (section 14), or from a controller that is offline, is `STALE` instead of `OK`. `FAULT` and `OUT_OF_RANGE` stay as they are. |
| `equipment`, `mode`, `estop`, `stopped`, `operation`, `latches`, `configVersion` | The latest `ZONE_STATE` (6.8), with its `outputs` under the name `equipment`. |
| `leak`, `activeCmdId` | The latest `ZONE_TELEMETRY` (6.1). |

A zone that has never reported also answers `200`: `online` is `false`; `lastSeen`, `ts`, every reading, `mode`, `estop`, `leak`, `stopped`, `activeCmdId`, `operation` and `configVersion` are `null`; `quality` and `equipment` are `{}`; `latches` is `[]`. The state is unknown, so start commands are refused (8.3 rule 7).

**`GET /devices`** returns every device in the layout, in order of device ID, as a list (11.1).

```json
{
  "items": [
    { "deviceId": "CC1", "kind": "CLIMATE_CONTROLLER", "zone": null, "online": true, "lastSeen": "2026-11-09T05:02:11.000Z" },
    { "deviceId": "ZC1", "kind": "ZONE_CONTROLLER", "zone": "Z1", "online": false, "lastSeen": null }
  ],
  "nextCursor": null
}
```

| Field | Rule |
|---|---|
| `deviceId`, `kind`, `zone` | From the layout (2.4). `zone` is a zone controller's zone, and `null` for any other device. |
| `online` | Same meaning as in `GET /zones/{zoneId}` above, for this device. `false` if it has never been heard from. |
| `lastSeen` | The newest `ts` of any message from the device. `null` if it has never been heard from. |

**`GET /climate`** answers the climate controller's latest readings and state, in the same way as the zone answer above. It always answers `200`, including when the controller is offline, with its last values and `"online": false`.

```json
{
  "online": true,
  "lastSeen": "2026-11-09T05:02:11.000Z",
  "ts": "2026-11-09T05:01:30.000Z",
  "readings": { "airTempC": 31.2, "airTempByPosC": { "PAD_END": 29.4, "MIDDLE": 31.0, "FAN_END": 33.1 }, "airRh": 71, "outsideTempC": 33.8, "outsideRh": 58, "elecRoomTempC": 34.0 },
  "quality": { "airTempC": "OK", "airRh": "OK", "outsideTempC": "OK", "outsideRh": "OK", "elecRoomTempC": "OK" },
  "mode": "MANUAL",
  "manualUntil": "2026-11-09T05:54:00.000Z",
  "stopped": false,
  "equipment": { "fanStage": 2, "FAN1": "ON", "FAN2": "ON", "FAN3": "OFF", "FAN4": "OFF", "PAD1": "ON", "SHADE1": "OPEN", "HAF1": "ON", "CH1": { "state": "ON", "setpointC": 22.0 }, "CH2": { "state": "ON", "setpointC": 22.0 }, "FOG1": "NOT_INSTALLED" },
  "latches": []
}
```

| Field | Rule |
|---|---|
| `online`, `lastSeen`, `ts`, `simTs` | As in the zone answer, for the climate controller (`climate.controller` in the layout). |
| `readings` | The latest values from 6.2, as sent. `airTempByPosC` is keyed by the layout's `climate.airSensors`. |
| `quality` | As in the zone answer: as sent, except that a value older than the fresh limit (section 14), or from a controller that is offline, is `STALE` instead of `OK`. |
| `mode`, `manualUntil`, `stopped`, `equipment`, `latches` | The latest `CLIMATE_STATE` (6.9). `manualUntil` is `null` when nothing will switch it back to `AUTOMATIC`. |

A climate controller that has never reported also answers `200`: `online` is `false`; `lastSeen`, `ts`, every reading, `mode`, `manualUntil` and `stopped` are `null`; `quality` and `equipment` are `{}`; `latches` is `[]`.

**`GET /power`** answers the power monitor's latest `POWER_STATE` in the same way, also `200` when it is offline.

```json
{
  "online": true,
  "lastSeen": "2026-11-09T05:02:11.000Z",
  "ts": "2026-11-09T05:02:11.000Z",
  "mains": "OK",
  "phases": { "l1V": 231, "l2V": 229, "l3V": 233 },
  "source": "MAINS",
  "generator": "STOPPED",
  "ats": "MAINS",
  "ups": { "onBattery": false, "batteryPct": 100, "runtimeMin": 42 },
  "quality": {}
}
```

| Field | Rule |
|---|---|
| `online`, `lastSeen`, `ts`, `simTs` | As in the zone answer, for the power monitor (`power.monitor` in the layout). |
| `mains`, `phases`, `source`, `generator`, `ats`, `ups` | The latest `POWER_STATE` (6.3), as sent. `generator` and `ups` are left out when the layout has `"generator": false` or `"ups": false`. |
| `quality` | `POWER_STATE` sends no quality, so this is `{}`, except that every value is `STALE` when the message is older than the fresh limit (section 14) or the monitor is offline. The phases keep their names from 6.3, and the UPS values take theirs from the units table in section 1: `{ "l1V": "STALE", "l2V": "STALE", "l3V": "STALE", "upsBatteryPct": "STALE", "upsRuntimeMin": "STALE" }`. |

A power monitor that has never reported also answers `200`: `online` is `false`; `lastSeen`, `ts`, `mains`, `source`, `ats` and each phase voltage are `null`; `generator` and `ups` are `null` when the layout has them; `quality` is `{}`.

Like the zone answer, neither answer carries the message envelope: no `msgId`, `bootId` or `seq`.

**`GET /commands/{cmdId}`** answers a command as its controller last reported it, from that command's newest `CMD_STATE` (6.6). From when the broker has the command until the first `CMD_STATE` arrives, it answers `DISPATCHED` (8.1), so the app can follow a command as soon as it is sent. `404` means no command with that ID has been sent or reported.

```json
{
  "cmdId": "6f1c2a90-3d1e-4c7b-9f52-2b8f0e6a1c44",
  "device": "ZC1",
  "state": "COMPLETED",
  "reason": null,
  "lines": [
    { "line": "A",    "requestedMl": 120, "deliveredMl": 119, "state": "DONE" },
    { "line": "B",    "requestedMl": 120, "deliveredMl": 121, "state": "DONE" },
    { "line": "ACID", "requestedMl": 30,  "deliveredMl": 30,  "state": "DONE" }
  ],
  "before": { "ec": 1.51, "ph": 6.21 },
  "after": { "ec": 1.63, "ph": 6.02 },
  "startedAt": "2026-11-09T04:55:01.000Z",
  "endedAt": "2026-11-09T05:00:12.000Z"
}
```

| Field | Rule |
|---|---|
| `cmdId` | The command's ID (7.1). |
| `device` | The controller whose `CMD_STATE` this is; `null` while `DISPATCHED`. A `STOP` that reaches several controllers (8.3 rule 10) shows the first controller to report it. |
| `state` | `DISPATCHED` until the first `CMD_STATE`, then that message's `state` (6.6). `UNKNOWN`, with `reason` `TIMED_OUT` (8.4), while the farm has left it without replies; a newer `CMD_STATE` shows the farm's state again. |
| `reason`, `lines`, `before`, `after`, `startedAt`, `endedAt` | The newest `CMD_STATE`, as sent; all `null` while `DISPATCHED`. |

Like the zone answer, it carries no message envelope.

**Alerts: `GET /alerts` and `POST /alerts/{alertId}/ack`.** The back end raises an alert from two sources. **Readings:** a zone's newest telemetry against `alerts.json`: `EC_HIGH` (EC above `zones.{zone}.alarms.ecMaxMsCm`), `SOL_TEMP_HIGH` (above `solTempMaxC`), `CIRC_NO_FLOW` (below `circFlowMinLpm`), `RESERVOIR_LOW` and `DAYTANK_LOW` (below `events.{kind}.thresholdPct`). Only a reading with quality `OK` counts. The alert is raised once the condition has lasted that kind's `delayS` (real time, 11.4) and cleared when a reading is back within the limit. **Events (6.5):** a `CRITICAL` or `WARNING` event raises an alert with its own `message`, `systemAction` and `nextStep`; its pair clears it (`ESTOP_RELEASED` clears `ESTOP_PRESSED`, and likewise `LEAK_CLEARED`, `CIRC_FLOW_RESTORED`, `SENSOR_RECOVERED`; `MODE_CHANGED` back to `REMOTE` clears `MODE_CHANGED`). One alert per condition (kind, zone, and line, metric or equipment) while it lasts. On a playground site, `POST /sim/reset` clears every alert and the list shows only alerts raised after the last reset.

`GET /alerts` takes `status` (`OPEN`, `ACKNOWLEDGED`, `CLEARED`), `severity` and `zone`, and answers the newest 200, newest first:

```json
{ "items": [ {
  "id": "3c2e…", "kind": "EC_HIGH", "severity": "WARNING", "status": "OPEN", "zone": "Z1",
  "message": "EC in Zone A is 2.6 mS/cm, above the 2.4 alarm limit",
  "systemAction": "No automatic action: the farm keeps running as it was",
  "nextStep": "Don't add nutrients; dilute with fresh water and check the EC probe",
  "raisedAt": "2026-11-12T05:10:04.000Z", "simTs": "2026-11-12T19:30:00.000Z",
  "clearedAt": null, "acknowledgedAt": null, "acknowledgedBy": null
} ] }
```

`status` is `CLEARED` once the condition is over, else `ACKNOWLEDGED` once someone has acknowledged it, else `OPEN`. `POST /alerts/{alertId}/ack` records who saw it and answers the alert; it doesn't clear it, and acknowledging again changes nothing. A Viewer gets 403 `FORBIDDEN` (13).

### 11.3 Dose request: example

Request:

```http
POST /api/dose-requests
{ "zone": "Z1", "lines": [ { "line": "A", "ml": 120 }, { "line": "B", "ml": 120 }, { "line": "ACID", "ml": 30 } ] }
```

Response `201`:

```json
{
  "id": "dr_8c2…",
  "zone": "Z1",
  "lines": [ { "line": "A", "ml": 120 }, { "line": "B", "ml": 120 }, { "line": "ACID", "ml": 30 } ],
  "projected": { "ec": 1.63, "ph": 6.0 },
  "checks": [
    { "id": "STATE_KNOWN",      "result": "PASS",  "message": "Zone 1 state known: nothing unconfirmed, not stopped" },
    { "id": "DEVICES_ONLINE",   "result": "PASS",  "message": "ZC1 and sensors online" },
    { "id": "ESTOP",            "result": "PASS",  "message": "No E-STOP pressed" },
    { "id": "ZONE_REMOTE",      "result": "PASS",  "message": "Zone 1 is in REMOTE" },
    { "id": "NO_CONFLICT",      "result": "PASS",  "message": "Nothing else running" },
    { "id": "LIMIT_DOSE",       "result": "PASS",  "message": "All lines within per-dose limits" },
    { "id": "LIMIT_24H",        "result": "PASS",  "message": "24 h totals within limits" },
    { "id": "DAYTANK",          "result": "WARN",  "message": "Day-tank B at 18%, refill soon" },
    { "id": "RESERVOIR",        "result": "PASS",  "message": "R1 at 78%" },
    { "id": "PROJECTED_EC_PH",  "result": "PASS",  "message": "Projected EC 1.63, pH 6.0 (target 1.5–1.8, 5.8–6.2)" },
    { "id": "FRESH_READINGS",   "result": "PASS",  "message": "EC/pH 40 s old" }
  ],
  "estimate": { "durationMin": 11, "totalMl": 270 },
  "canConfirm": true
}
```

`result` is `PASS` · `WARN` · `BLOCK`. `canConfirm` is false if any check is `BLOCK`. The app enables **Start dose** only when nothing is `BLOCK` and the user has ticked the confirmation box.

Confirm response `202`: `{ "cmdId": "6f1c…", "state": "DISPATCHED" }`. After that, the app follows the command over the WebSocket.

### 11.4 Simulator control (playground only)

Available only on sites whose config has `"features": { "playground": true }`. The back end forwards these calls to the simulator’s own small HTTP control API and records each one in the audit log. Every screen on a playground site shows a **“Simulated farm”** banner with the current speed.

| Method and path | Purpose | Roles |
|---|---|---|
| `GET /sim/state` | Current scenario, simulated clock and date, speed, weather, active failure drills, held values | All |
| `POST /sim/speed` `{ "x": 60 }` | Clock speed: 0 (pause), 1, 60 or 288 (one day in five minutes) | Owner, Agronomist, Operator |
| `POST /sim/faults` `{ "kind": "LEAK", "target": "Z1", "on": true }` | **Trigger** a failure drill (`"on": true`) or **Fix** it (`"on": false`). The catalogue is in 11.4.3 | Owner, Agronomist, Operator |
| `POST /sim/holds` `{ "target": "Z1", "metric": "ph", "value": 7.2, "gradual": true }` | **Set a value and hold it** (11.4.2) | Owner, Agronomist, Operator |
| `DELETE /sim/holds/{target}/{metric}` · `POST /sim/holds/release-all` | **Release** one held value, or all of them | Owner, Agronomist, Operator |
| `POST /sim/reset` | Back to the start of the current scenario: all faults off, levels and readings reset | Owner |
| `GET /sim/scenario-options` | The presets and allowed ranges from `scenarios.json` for the “Set up your farm” screen, and the **“Things to try”** list (label, one-line hint, and the fault or screen it relates to) shown in the Simulator screen | All |
| `POST /sim/scenario` (body below) | Start a run from these starting conditions: resets the simulated farm, sets the clock and weather, plants the chosen crops | Owner, Agronomist, Operator |
| `GET /sim/scenarios` · `POST /sim/scenarios` · `DELETE /sim/scenarios/{id}` | List, save and delete named farm setups (layout, environment and starting conditions) so a user can load them again | Read: all · save/delete their own: Owner, Agronomist, Operator |

**Scenario (starting conditions).** Every value must be one of the options or inside the range given by `scenarios.json`; anything else is refused with `422`. Nothing here is fixed in code.

```json
{
  "name": "Monsoon week · basil · flaky internet",
  "region": "COIMBATORE",
  "month": 7,
  "startTime": "06:00",
  "speed": 60,
  "weather": "MONSOON_RAIN",
  "surprises": ["HEAT_SPIKE"],
  "zones": {
    "Z1": { "crop": "LETTUCE", "stage": "GROWING", "benchesPlanted": 4, "ec": "NORMAL", "ph": "HIGH", "solTemp": "NORMAL", "reservoir": "HALF" },
    "Z2": { "crop": "BASIL",   "stage": "SEEDLING", "benchesPlanted": 2, "ec": "LOW",    "ph": "NORMAL", "solTemp": "WARM",   "reservoir": "FULL" }
  },
  "dayTanks": "HALF",
  "rawWater": "HARD",
  "power": "OCCASIONAL_CUTS",
  "internet": "FLAKY",
  "layout": { "zones": [ "…" ], "nursery": { "tables": 6 }, "climate": { "fans": ["FAN1", "FAN2", "FAN3", "FAN4"], "padPumps": ["PAD1"], "chillers": { "Z1": "CH1", "Z2": "CH2" }, "shade": ["SHADE1"], "haf": ["HAF1"], "foggers": [], "airSensors": ["PAD_END", "MIDDLE", "FAN_END"] }, "power": { "generator": true, "ups": true } }
}
```

| Field | Options (examples; the real lists come from `scenarios.json`) | What it changes in the simulation |
|---|---|---|
| `region` | `COIMBATORE`, `COASTAL_HUMID`, `HOT_DRY_INLAND`, `HILL_STATION` | Outside temperature and humidity range |
| `month` | 1–12 | Day length, heat, humidity, rain for that region |
| `startTime`, `speed` | Any time; ×1, ×60, ×288 | Where in the day the run starts and how fast it passes |
| `weather`, `surprises` | `CLEAR`, `CLOUDY`, `HEATWAVE`, `MONSOON_RAIN`, `HUMID_SPELL`; `HEAT_SPIKE`, `COLD_RAINY_DAY` | Load on fans, pads, shade and chillers; humidity alerts |
| `zones.*.crop`, `stage`, `benchesPlanted` | Crops from the client’s `recipes.json`; `SEEDLING`, `GROWING`, `READY`; 0 to the number of benches | EC and pH targets, how fast plants draw nutrients and water |
| `zones.*.ec`, `ph`, `solTemp`, `reservoir`; `dayTanks` | `LOW`, `NORMAL`, `HIGH` (temperature `COOL`/`NORMAL`/`WARM`); levels `LOW`, `HALF`, `FULL` | Whether the user must act straight away; how soon refills and low-stock alerts come |
| `rawWater` | `SOFT`, `HARD`, `HIGH_BICARBONATE` | How fast pH climbs and how often acid is needed |
| `power`, `internet` | `STABLE`, `OCCASIONAL_CUTS`, `FREQUENT_CUTS`; `STABLE`, `FLAKY`, `LONG_OUTAGE` | Power cuts, generator and UPS events; internet drops and “Result unknown” during the run |
| `layout` | Within `layoutOptions` in `scenarios.json` (11.4.1) | The farm itself: zones, tanks, benches, equipment and sensors. Shows what each piece of equipment is worth |

**Time on playground sites.** The simulator’s clock can run up to 288× faster. Every message’s `ts` stays **real time**, so staleness, command expiry, offline detection and alert timers work exactly as on a real farm. The simulated time travels in `simTs`: on playground sites, charts and history plot `simTs`, so they show the simulated day. The app shows the simulated date and time next to the “Simulated farm” banner.

**Reading rate at high speed.** Periodic readings (`ZONE_TELEMETRY`, `CLIMATE_TELEMETRY`) are at most one per `minRealReadingIntervalS` real seconds (`simulator.json`, 5 s for client_A), so a fast clock can't flood the broker:

```
interval in simulated seconds = max(60, speed × minRealReadingIntervalS)
```

| Speed | Interval (simulated) | Interval (real) |
|---|---|---|
| ×1 | 60 s | 60 s |
| ×60 | 300 s | 5 s |
| ×288 | 1440 s | 5 s |
| 0 (pause) | None | None |

The farm itself still moves on every tick, and charts simply have fewer points per simulated hour at high speed. Not affected: readings sent at once when a limit is crossed, `ZONE_STATE` and `CLIMATE_STATE` on every change, heartbeats (30 real seconds) and `POWER_STATE` (60 real seconds). While paused, heartbeats keep the devices online, but after 120 s the readings are too old to dose on, so doses are blocked (8.3 rule 7) until the clock runs again.

**24-hour totals follow simulated time.** They limit what the water and plants receive, so they belong to the simulated day: at ×288 a simulated day takes five minutes and dosing limits must reset with it, or the farm could never be dosed properly. Both the controller's counters and the cloud's `LIMIT_24H` check use a rolling 24 hours of `simTs` on playground sites (`ts` on real farms), counting delivered mL. The same goes for `mixWaitS`, `maxDurationS` and `runTimeTodayMin`, which also measure the farm. Everything that measures the system stays on real time, as above. `POST /sim/reset` and `POST /sim/scenario` clear the totals: the controllers forget theirs, and the cloud keeps the time of the last reset and counts only commands reported after it.

Starting conditions are **illustrative, not an agronomic forecast**: the app says so on the setup screen. Plant growth may show visually on the Farm view, but no yield numbers are produced. Events during a run use `POST /sim/faults` and `POST /sim/holds`.

A fault only changes what the simulated farm does or reports. Everything the user then sees (blocked doses, alerts, history, reports) comes through the normal contract messages and back-end rules, exactly as it would on a real farm.

#### 11.4.1 Playground flow and setup

**The flow (agreed):**

1. The user **signs in**. Playground sites have **two screens**, which can be open side by side: the **Control panel** (the normal app, the people’s side) and the **Simulator** screen (the farm’s side). The Simulator screen exists only on playground sites.
2. On the Simulator screen, the user goes through the **setup steps**:
   1. **Farm layout:** for example the number of zones, growing tables (or benches and channels) per zone, and which equipment and sensors are fitted.
   2. **Environment and starting conditions:** region, month, start time, weather, crops and stages, starting water state, raw water, power and internet reliability.
   3. **Review and start.** The simulator builds the farm from this setup and the Farm view draws it.
3. The user **plays**:
   - **On the Control panel (the people):** dose, refill, stop, resume, climate. These are the same commands, checks and states as everywhere else.
   - **On the Simulator screen (the farm):** set a value and hold it (11.4.2), trigger and fix failure drills (11.4.3), press the E-STOP, change the weather, speed, pause and reset. The Simulator screen has **no buttons for people’s actions** (no dose, refill or stop), and the Control panel has no farm-side controls.
   - **Watch the farm:** the Farm view (11.4.4) on the Simulator screen shows pumps, valves, tanks, fans and sensors changing live, and a **farm activity** list shows each command the farm received, who sent it and the controller’s answer (from `GET /operations`).

   Every Simulator-screen action goes through the back end to the simulator (signed in, role-checked, audit-logged); the simulator’s own control API is never exposed. The results come back as normal contract messages, so every tab (Overview, Zone, Alerts, History) reacts as it would on a real farm.
4. **Save (optional):** the user can save their farm setup (layout, environment and starting conditions) under a name and load it again later (`/sim/scenarios`). Saved setups are stored in the database for that user and organisation, not as files in the repo. Real client sites keep their config packs and are not affected.

**Setup contents (OPEN-15, decided):**

- **Where the layout comes from (agreed).** On real sites, from the client’s config pack (set at commissioning). On playground sites, from the layout the user edits on the Simulator screen, sent with `POST /sim/scenario` and stored by the back end. The simulator never announces its own layout.
- **What the layout setup contains.** `POST /sim/scenario` carries a `layout` block with the same field names as `layout.json` (2.4): `zones` (`id`, `name`, `reservoir.usableL`, `doseLines`, `sensors`, `dayTanks`, `benches`), `nursery`, `climate` (`fans`, `padPumps`, `chillers`, `shade`, `haf`, `foggers`, `airSensors`) and `power` (`generator`, `ups`). The back end assigns the IDs that come from the zone number: controller, reservoir, dosing cabinet and `devices`. Each value must be inside `layoutOptions` in `scenarios.json`, otherwise `422`. Polyhouse size is left out: nothing reads it yet, and it can be added later as an optional field. `layout` replaces the earlier `equipment` field.
- **The list of scenarios and presets** is config, not contract: `scenarios.json` holds it (`presets` holds named scenario bodies), and the current file is the starting list.
- **Who may save setups:** Owner, Agronomist and Operator save and delete their own. Everyone can read. A Viewer can't start a farm, so can't save one.

**Rule:** all of these lists live in the client’s `scenarios.json`. `GET /sim/scenario-options` returns them, the app builds the setup steps and the playground controls from that response, and the simulator validates against the same file. Adding or removing an item is then a **config change, not a contract or code change**.

**Several users at once (OPEN-16, decided):** each user gets their **own sandbox playground farm**, so one person’s setup never changes another person’s farm.

| Rule | Detail |
|---|---|
| Created | By `POST /sim/scenario`. Starting again replaces that user’s sandbox. |
| What it is | One simulator run with its own site slug and its own device logins. Topics and permissions (section 4) apply per site, so nothing else in this contract changes. |
| Which farm an API call means | The signed-in user’s sandbox, from the session. No URL or message from the app names it. A user with no sandbox, Viewers included, sees the site’s own shared playground farm, read-only. |
| Removed | After `sandbox.idleRemoveMin` minutes without a call from that user (`scenarios.json`). Saved setups stay, so the user can start it again. |
| Cap | At most `sandbox.maxRunning` at once (`scenarios.json`). Over the cap, `POST /sim/scenario` answers `409` with code `SANDBOX_LIMIT`, and the app says to try again later. |
| Until sandboxes are built | One simulator running one farm is the same contract with a single site, used by one person at a time. |

#### 11.4.2 Set a value and hold it

The user can set a reading to a value and keep it there, to see how the app, the alerts and the controllers react. The values that can be set, and their ranges, come from `scenarios.json` (the sensors’ full measuring ranges, for example EC 0–10 mS/cm, pH 0–14, solution temperature 0–50 °C, reservoir level 0–100 %).

| Rule | Detail |
|---|---|
| What can be set | Per zone: `ec`, `ph`, `solTempC`, `circFlowLpm`, `reservoirPct`, `dayTankPct.A` / `.B` / `.ACID` (from the zone’s `doseLines`). Polyhouse: `airTempC`, `airRh`. |
| It changes the farm, not just the sensor | A held value changes the **simulated water or air itself**, so the controllers’ own safety logic reacts too (for example a held low reservoir blocks dosing). To fake **only the sensor** (a faulty probe), use a failure drill instead (11.4.3: `PROBE_DRIFT`, `STUCK_READING`, `IMPOSSIBLE_READING`, `SENSOR_OFFLINE`). |
| Gradual by default | With `"gradual": true` (default), EC, pH and temperature move to the new value over about two readings, as real water would, so a “changed faster than water can” rule does not mistake it for a faulty probe. `"gradual": false` jumps at once. |
| Held until released | The simulator keeps the value there against drift until the user releases it (`DELETE /sim/holds/…` or release all). The controllers keep reporting it, and the back end raises the matching alerts. |
| Released when people act on it | A dose releases `ec`, `ph` and the dosed day-tanks; a refill releases `reservoirPct` (and `ec`, `ph`, which dilute); a chiller command releases `solTempC`. The action then has its real effect. |
| Air is a load, not a pin | Raising `airTempC` (or `airRh`) adds a lasting heat load (or moisture), like strong sun or closed vents. It is not pinned: when the user raises the fan stage, turns on the pads or closes the shade, the temperature really falls. Releasing removes the load. |
| Shown and logged | Held values appear in `GET /sim/state`, in the Simulator screen and on the Farm view, and every set and release is written to the audit log. |

#### 11.4.3 Failure drills

Each drill has a **Trigger** and a **Fix**. Trigger breaks the part; Fix repairs it. Anything the farm or the back end locked out because of it (a stopped zone, a blocked dose, an open alert) still waits for a person to act in the app, as on a real farm. The catalogue lives in `scenarios.json`, with for each drill: `kind`, a title, a one-line explanation, what **Fix** does, its target (zone, zone sensor, dosing line, polyhouse or device), any parameters, and its code in the PRD’s failure list. Starting catalogue:

| Group | `kind` (PRD failure code) |
|---|---|
| Sensing | `PROBE_DRIFT` (F1) · `IMPOSSIBLE_READING` (F2) · `STUCK_READING` (F3) · `SENSOR_OFFLINE` · `DAYTANK_SENSOR_STUCK` (F4) |
| Chemistry | `STOCK_STRENGTH` weak or strong stock (F5) · `LINES_SWAPPED` (F7) · `WRONG_STOCK` (F22) · `EC_DRIFT_LOW` · `EC_DRIFT_HIGH` · `PH_DRIFT` |
| Pumps and valves | `NO_FLOW` pump runs, nothing flows (F8) · `PUMP_STUCK_ON` stuck relay (F9) · `REFILL_VALVE_STUCK_OPEN` (F10) · `CIRCULATION_FAILURE` (F11) · `CHILLER_FAULT` runs, no cooling (F12) |
| Water | `NO_WATER_SUPPLY` (F29) · `HIDDEN_LEAK` unexplained level drop (F30) · `LEAK` leak sensor wet (F17) · `RESERVOIR_LOW` · `DAYTANK_LOW` · `DRAIN_TO_LOW_LOW` (F18, one-off) · `BIG_REFILL` dilutes the reservoir (F31, one-off) |
| Climate and rooms | `FAN_FAILURE` · `PAD_PUMP_FAILURE` · `PAD_CLOGGED` · `HEATWAVE` (F20) · `HOT_AFTERNOON` · `ELECTRICAL_ROOM_HOT` (F19) |
| Safety and modes | `ESTOP` press / release (F23) · `ZONE_MODE` (`value`: `LOCAL` or `MAINT`) |
| Power | `POWER_CUT` controller restarts with all outputs off (F15) · `GENERATOR` · `UPS_LOW` · `REBOOT_MID_DOSE` |
| Connectivity and data | `LINK_DROP` internet lost, now or right after the next dose (F13, F14, F28) · `DEVICE_OFFLINE` · `SENSOR_STALE` · `CLOCK_DRIFT` (F16) |

Adding, removing or rewording a drill is a change to `scenarios.json`, not to this contract. A drill only changes what the simulated farm does or reports: everything the user then sees comes through the normal contract messages and back-end rules.

#### 11.4.4 Farm view

On playground sites the **Simulator screen** shows a **2D Farm view**:

- **Polyhouse plan:** benches and channels (flowing or not), cooling pads, exhaust fans (running stage), shade, HAF fans, foggers, air sensors with their readings, the outside weather sensor and the electrical room.
- **One process diagram per zone:** dosing cabinet with the Stock A, Stock B and acid day-tanks and pumps, E-STOP, raw-water refill valve, reservoir with its level and the low-low and high-high marks, EC, pH, temperature (and DO, if fitted) probes, chiller, leak sensor, circulation pump and channel flow.
- **Clicking a sensor or tank** opens its set-value control (11.4.2); held values and active drills are marked on the drawing.

It is drawn from `GET /farm` (layout) and moved only by the normal live data: readings and equipment states from 6.1 and 6.2 over the WebSocket, plus `ZONE_STATE` and `CLIMATE_STATE` (6.8, 6.9), which arrive on every change, and `sim_state` (section 12). No extra endpoint is needed, and nothing in the drawing is fixed in code: it follows the layout config, for 1 zone or more.

---

## 12. WebSocket (live updates to the app)

- URL: `wss://{host}/api/live`. Authenticated by the session cookie.
- The server sends only what the user's role may see.
- Server sends `{ "type": "PING" }` every 30 s; the client replies `{ "type": "PONG" }`. The server closes a connection after two unanswered PINGs. The client reconnects with a growing delay (1, 2, 4 … up to 30 s).
- Nothing is replayed over the socket. After a reconnect, the client refetches what it shows.
- The app derives what it needs from these messages: a zone is offline when `device_state` says its controller is, and the farm is offline (the Live chip, section 14) when every device is.

Every message: `{ "type": "…", "ts": "…", "data": { … } }`. `ts` is when it happened, in real time. On playground sites it also has `simTs`, which charts and history plot (11.4).

| `type` | `data` |
|---|---|
| `reading` | `{ "zone": "Z1", "readings": {…}, "quality": {…} }` or climate readings with `"zone": null`. Messages replayed after a link drop are stored but not sent as `reading`; the app refetches when the device comes back. |
| `command_state` | `{ "cmdId", "kind", "zone", "state", "reason", "lines", "before", "after", "detail" }`. Sent again with the same state when `after` arrives (6.6). For a `STOP`, `zone` is the stopped zone or `null`. |
| `alert` | `{ "id", "kind", "severity", "status", "zone", "message", "systemAction", "nextStep" }` (PRD rule 7) |
| `zone_state` · `climate_state` | Same fields as 6.8 and 6.9, sent on every change |
| `device_state` | `{ "deviceId", "online", "lastSeen", "fw" }`, sent when `online` changes. `online` means the same as in 11.2. |
| `power_state` | Same fields as 6.3 |
| `mode_change` | `{ "zone": "Z1", "mode": "MAINT" }` or `{ "climate": "MANUAL", "manualUntil": "…" }`. A prompt for a banner; `zone_state` and `climate_state` stay authoritative (6.8, 6.9). |
| `sim_state` | Playground sites only: `{ "speed", "simTime", "faults": [ … ], "holds": [ … ] }`, sent when any of them changes |

---

## 13. Roles and permissions (summary)

| Action | Owner | Agronomist | Operator | Viewer |
|---|---|---|---|---|
| See everything | ✔ | ✔ | ✔ | ✔ |
| Stop all operations, Stop zone, Stop all equipment (no confirmation) | ✔ | ✔ | ✔ | ✔ |
| Resume after a stop | ✔ | ✔ | ✔ | — |
| Clear a latched fault | ✔ | — | — | — |
| Dose, refill, climate mode and equipment | ✔ | ✔ | ✔ | — |
| Acknowledge alerts, crop cycles, record day-tank refills, export | ✔ | ✔ | ✔ | — |
| Edit recipes, apply a recipe, auto-refill settings | ✔ | ✔ | — | — |
| Propose safety-limit changes | ✔ | ✔ | — | — |
| Approve, reject or ask for changes on limits | ✔ | — | — | — |
| Device diagnostics | ✔ | — | — | — |
| Users and roles, farm layout | ✔ | — | — | — |

Role-limited controls are **shown disabled with a lock icon and a one-line reason, never hidden** (handoff). The server enforces every rule; a disabled button is never the protection. There is no Engineer role in this phase; the team uses Owner access during build and maintenance.

---

## 14. Timing constants

| Constant | Value | Who uses it |
|---|---|---|
| Telemetry interval | 60 s (immediately on alarm). Zone and climate readings on playground sites: `max(60, speed × minRealReadingIntervalS)` simulated seconds (11.4) | ZC, CC1, PM1 |
| Heartbeat (`DEVICE_STATUS`) | 30 s | All devices |
| Device **stale** | 3 missed heartbeats (90 s) | Cloud |
| Device **offline** | 3 minutes without any message, or last will received | Cloud |
| Farm **offline** | 3 minutes with nothing from any device: the app's Live chip turns to Offline and every reading is treated as stale | Cloud and app |
| Fresh EC/pH to dose | ≤ 120 s old | Cloud and ZC |
| Command expiry | 60 s from `issuedAt`, `STOP` `ALL_EQUIPMENT` included | Cloud sets, ZC and CC1 enforce |
| `STOP` expiry (`ALL_OPERATIONS`, `ZONE`) | 5 minutes from `issuedAt` (7.4) | Cloud sets, ZC enforces |
| `DISPATCHED` → `UNKNOWN` | At `expiresAt`, if the controller hasn't been heard from since dispatch (8.3) | Cloud |
| `ACCEPTED` → `UNKNOWN` | 2 minutes with no `CMD_STATE` while the farm is silent | Cloud |
| No reply → `UNKNOWN`, `TIMED_OUT` | 30 s after `expiresAt`, or after the command's newest `CMD_STATE` if that came later, with no final state; checked every 10 s (8.4) | Cloud (worker) |
| Progress while dosing | at least every 5 s | ZC |
| Dose `maxDurationS` | 600 s (default; configured in `limits.json`) | ZC |
| Mix wait before `after` reading | 300 s (default; configured in `limits.json`) | ZC |
| Climate equipment confirmation | 30 s, then “No response from device” | App |
| Climate manual | 5–240 minutes | CC1 |
| 24-hour dose totals | Rolling 24 hours of delivered mL, on `simTs` on playground sites (11.4) | Cloud and ZC |

---

## 15. Open questions

| # | Question | Owner |
|---|---|---|
| OPEN-2 | Resolved by the handoff: chillers switch on/off and take a setpoint (18–28 °C) in Manual mode. | — |
| OPEN-3 | Are the roll-up curtains motorised? If so, add `CURT*` equipment. | Project team (vendor) |
| OPEN-4 | Is a pH-up line needed (`BASE`)? | Agronomy |
| OPEN-5 | Real numbers: 24 h limits, EC ceiling, pH bounds, EC/pH targets, minimum reservoir (placeholders until then; the 300 mL per-dose limit and 50 L day-tanks come from the handoff). | Agronomy |
| OPEN-8 | Resolved: every role, Viewer included, may use every Stop button, with no confirmation dialog (PRD rule 1). The app warns afterwards that the house will heat up. | — |
| OPEN-9 | Resolved: 14 states, including `REJECTED`, `STOPPED` and the reserved `AWAITING_2ND_APPROVAL`, with the “App shows” wording in 8.1. `STOPPED` no longer says “by user”, because the E-STOP, key switch or a fault can also stop a command. | — |
| OPEN-10 | Resolved: `STOP` `ALL_OPERATIONS` and `ZONE` expire after 5 minutes, and `ALL_EQUIPMENT` keeps the normal 60 s (7.4, section 14). | — |
| OPEN-11 | **Several clients later:** add an organisation layer (organisation → sites → zones). Proposed: every database table, API call and message carries `org_id` / `site_id` from day one, even with one value; users and data are kept separate per organisation. | Track 1 |
| OPEN-12 | **No fixed equipment assumptions:** the highest fan stage equals the number of fans in the layout config, and dosing lines come from `doseLines` in the config. No code, screen or check may assume exactly 4 fans or exactly A, B and ACID. | Tracks 1, 2, 3 |
| OPEN-14 | **Per-site feature switches:** a `features` block in each site’s config (for example `playground`, `demoFarm`, `climateAutomatic`, `autoRefill`, `secondApproval`, `foggers`; see 2.5), read by the API and the app, so each client can turn features on or off without code changes. | Tracks 1 + 2 |
| OPEN-15 | Resolved: the `layout` block uses the fields of `layout.json`, within `layoutOptions`; scenarios and presets are config in `scenarios.json`; Owner, Agronomist and Operator save and delete their own setups (11.4.1). | — |
| OPEN-16 | Resolved: one private sandbox farm per user, created at setup, removed after inactivity, with a configured cap on how many run at once (11.4.1). | — |

---

## 16. Changing this contract

1. Changes to this contract must be made by **pull request** to `docs/contract.md`, together with the matching JSON Schema and example files in `docs/contract/schemas/` and `docs/contract/examples/`.
2. The pull request needs approval from Track 1, Track 3 and Track 4 (and Track 2 for sections 11–13).
3. **Additive** changes (a new optional field, a new event kind, a new reason code) keep `"v": 1`. Receivers already ignore unknown fields.
4. **Breaking** changes (renaming, removing or changing the meaning of a field) bump `"v"`. The back end accepts both versions until all devices are updated.
5. CI validates every example against its schema. The simulator and back end load the same schemas, so a mismatch fails the build.
6. Record every change in the changelog below.

## 17. Changelog

| Version | Change | By |
|---|---|---|
| v0 | First draft for the contract session, built from the tech stack, app and back-end guides and the revamp layout. | Project team |
| v0.1 | Aligned with the Phase 2A Build Handoff: web app only, four roles, zone names A/B mapped to Z1/Z2, fan stage, shade, HAF fans, chiller setpoint, air temperature by position, dissolved oxygen, 3-minute offline, 5 % mismatch, 50 L day-tanks, Stop for every role, second approval off, dose estimate endpoint. | Project team |
| v0.2 | Added scalability decisions OPEN-11 to OPEN-14 (organisation and site IDs, no fixed equipment counts, gateway device adapter, per-site feature switches); fan stage follows the number of fans. | Project team |
| v0.3 | Re-phased: Phase A = web app, back end, cloud and farm simulator with playground; Phase B = firmware and gateway; Phase C = real farm. Added simulator control endpoints (11.4), what the simulator must implement (9) and its gateway emulation (10). | Project team |
| v0.4 | Phase A is manual only: climate Automatic and auto-refill switched off by feature switches (`FEATURE_OFF`); Phase D added for automation (the handoff’s 2B); all farm values in per-client configuration packs (2.5, `config/clients/client_A/`). | Project team |
| v0.5 | “Set up your farm” scenarios: starting conditions (region, month, time, weather, crops and stages, water state, raw water, power and internet reliability, equipment) via `POST /sim/scenario`, presets in `scenarios.json`, saved scenarios. Time rule for playground sites (simulated `ts`, arrival time for staleness and expiry). | Project team |
| v0.6 | Dropped the auto-playing demo story from Phase A (`POST /sim/story` removed); added a “Things to try” list from `scenarios.json`; pause = speed 0. | Project team |
| v0.6.1 | Phase A broker not exposed to the internet; Phase A alerts are in the app only. | Project team |
| v0.7 | Phase A edition: every device is played by the simulator; sections 3, 9 and 10 describe only what the simulator does; broker internal only; later-phase items (firmware, real gateway, local mode, third-party hardware) moved out to `phase-2-later-phases.html`; OPEN-1, 6, 7 and 13 moved there too. | Project team |
| v0.7.1 | Playground flow added (11.4.1): sign in → setup steps (layout, environment, starting conditions) → play with the control panel and playground actions → optionally save the farm setup. Layout fields, “set value” actions and the scenario list kept open (OPEN-15); sandbox per user proposed (OPEN-16). | Project team |
| v0.7.2 | Simulated controllers built firmware-style behind a HAL, with realistic simulated hardware; `hardware.json` and `io_map.json` added to the config pack (2.5); test vectors noted in §9. | Project team |
| v0.7.3 | Playground, based on the virtual-farm prototype: set a value and hold it (11.4.2, `/sim/holds`), failure-drill catalogue with Trigger and Fix and PRD codes (11.4.3), 2D Farm view and farm activity list (11.4.4), `sim_state` WebSocket message; OPEN-15 narrowed; dates removed. | Project team |
| v0.8 | Aligned with the PRD rules and the virtual-farm design: two screens on playground sites (Control panel and Simulator screen, 11.4.1); `ZONE_STATE` and `CLIMATE_STATE` sent on every change (6.8, 6.9); `RESUME` and `CLEAR_FAULT` (7.8, 7.9) and “stopped stays stopped”; “unknown means blocked” (8.3, `STATE_UNKNOWN` check); no confirmation for any stop (OPEN-8 resolved); events and alerts carry `message`, `systemAction`, `nextStep`; `ts` is real time with `simTs` for simulated time; layout source agreed. | Project team |
| v0.8 lock | Items A to F locked; fan IDs and top stage from config; `ZONE_STATE` authoritative where it overlaps telemetry. | Project team |
| v0.8 part 1 | Configured defaults, `503` for the health check, optional power fields, quality rule (T1-9). | Track 1 |
| v1.0 | Contract part 2 (T1-10): `CMD_STATE` fields, mix wait and state-to-reason table (6.6); `RESUME` and `CLEAR_FAULT` topics (4, 7.8, 7.9); lifecycle per command kind, `UNKNOWN` resolution, `FAULT_DURING` and `CANCELLED` (8); check order and persistence (9); link-drop behaviour (10); `GET /farm` and `GET /zones/{zoneId}` responses (11.2); reading rate and 24-hour totals on simulated time (11.4); WebSocket rules (12); `dayTankL` to 0.1 L (1); climate alarm limits (6.2); OPEN-9, 10, 15 and 16 resolved. All changes keep `"v": 1` (16). | Track 1 |
| v1.0, 6.9 example | JSON example for `CLIMATE_STATE` (6.9), agreed in the T1-14 review. The message itself doesn't change, so it keeps `"v": 1` (16). | Track 1 |
| v1.0, devices and error codes | `GET /devices` fields (11.2), general error codes (11.1) and `upsRuntimeMin` (1), agreed in the T1-12 review. MQTT messages don't change, so they keep `"v": 1` (16). | Track 1 |
| v1.0, climate and power answers | `GET /climate` and `GET /power` answers (11.2), in the style of the zone answer (T1-18). MQTT messages don't change, so they keep `"v": 1` (16). | Track 1 |
| v1.0, command answer | `GET /commands/{cmdId}` answer (11.2), with `DISPATCHED` until the first `CMD_STATE`, agreed in the T1-17 review. MQTT messages don't change, so they keep `"v": 1` (16). | Track 1 |
| v1.0, stuck commands | `TIMED_OUT` (8.4): a command the farm leaves without replies answers `UNKNOWN` with that reason (11.2), and when (14), from T1-23. MQTT messages don't change, so they keep `"v": 1` (16). | Track 1 |

### v1.0 section review

| Section | v1.0 | What changed |
|---|---|---|
| 0 Scope | No change | |
| 1 Conventions | Changed | `dayTankL` to 0.1 L, `reservoirL` whole litres |
| 2 Identifiers and layout | Changed | 2.3 table separator fixed; `devices` noted in 2.4; two lines in the 2.5 file list |
| 3 Transport | No change | |
| 4 Topics | Changed | `RESUME` and `CLEAR_FAULT` use the `cmd` topics; ACL unchanged |
| 5 Envelope | No change | |
| 6 Farm → cloud | Changed | 6.1 and 6.2 reading rate pointer; 6.2 alarm limits; 6.6 `CMD_STATE`; 6.8 `operation` in the mix wait. 6.3, 6.4, 6.5, 6.7 and 6.9: no change |
| 7 Cloud → farm | Changed | 7.2 cloud-only limits; 7.4 stop expiry; 7.8 and 7.9 topics and climate target. 7.1, 7.3, 7.5, 7.6 and 7.7: no change |
| 8 Command lifecycle | Changed | Wording, transitions, rules 9–11, two reason codes |
| 9 Controller rules | Changed | Rules 1, 2, 3 and 8 |
| 10 Gateway rules | Changed | Rules 2 and 4; new rules 6–8 |
| 11 REST API | Changed | 11.2 responses and climate clear-fault; 11.4 reading rate, 24-hour totals, `layout`, sandboxes. 11.1, 11.3, 11.4.2, 11.4.3 and 11.4.4: no change |
| 12 WebSocket | Changed | Reconnect, `simTs`, replayed messages, `command_state` fields |
| 13 Roles | No change | |
| 14 Timing | Changed | Stop expiry, `DISPATCHED` → `UNKNOWN`, reading rate, 24-hour totals |
| 15 Open questions | Changed | OPEN-9, 10, 15 and 16 resolved; OPEN-3, 4 and 5 stay open |
| 16 Changing this contract | No change | |
| 17 Changelog | Changed | This entry |
