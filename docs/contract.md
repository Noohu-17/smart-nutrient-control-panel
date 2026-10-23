# Smart Nutrient Advisor: interface contract

**Version:** v0.7 (draft for review, Phase A edition) · **Date:** 23 Oct 2026 · **Lives at:** `docs/contract.md`
**Owners:** Track 1 (back end and cloud, editor) · Track 3 (simulator farm behaviour and controller rules) · Track 4 (simulator core and playground control) · Track 2 (app, sections 11–13)
**Status:** DRAFT. Nothing here is final until the contract session approves it. Items marked **[OPEN]** need a decision; see section 15.

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
| Time | ISO-8601 in **UTC** with `Z`, millisecond precision: `"2026-11-09T04:54:00.000Z"`. People see IST; machines store UTC. |
| IDs | Commands and messages use **UUID v4** strings. Farm things use the fixed IDs in section 2. |
| Units | Fixed per field (table below). Units are never sent in the value. |
| Missing or failed sensor | Send `null` for the value and set its quality (section 6.1). Never send a fake 0. |
| Unknown fields | Receivers **ignore** fields they don't know, so additive changes don't break anyone. |
| Version | Every message carries `"v": 1`. A breaking change bumps it (section 16). |
| Numbers | Plain JSON numbers, no strings. Volumes are whole millilitres. |

### Units

| Quantity | Unit | Precision | Example field |
|---|---|---|
| Electrical conductivity | mS/cm | 0.01 | `ec` |
| pH | pH | 0.01 | `ph` |
| Temperature | °C | 0.1 | `solTempC`, `airTempC` |
| Relative humidity | % | 1 | `airRh` |
| Level | % of usable volume | 1 | `reservoirPct` |
| Volume in a tank | L | 1 | `reservoirL` |
| Dosed volume | mL | 1 | `ml` |
| Flow | L/min | 0.1 | `flowLpm` |
| Voltage | V | 1 | `l1V` |
| Battery | % | 1 | `upsBatteryPct` |
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
|---|---|---|
| `{zone}-DOSE-A`, `{zone}-DOSE-B`, `{zone}-DOSE-ACID` | Dosing lines (pump + flow meter) | ZC | Only through a `DOSE` command |
| `{zone}-DT-A`, `{zone}-DT-B`, `{zone}-DT-ACID` | Day-tanks (concentrate) | ZC | — (level only) |
| `{zone}-CIRC` | Circulation pump | ZC | **Never.** No command may stop it. |
| `{zone}-REFILL` | Reservoir refill valve or pump | ZC | Only through a `REFILL` command |
| `FANS` (`FAN1` … `FAN4`) | 4 × 42″ exhaust fans, staged 0–4 | CC1 | Stage 0–4, in climate MANUAL mode only. Turning fans to 0 also stops the pad pump. |
| `PAD1` | Cooling pad pump | CC1 | On/off in MANUAL mode only; needs fans at stage 1 or more |
| `CH1`, `CH2` | Solution chillers (C1 → Z1, C2 → Z2 on the revamp plan) | CC1 | On/off and setpoint 18–28 °C, in MANUAL mode only |
| `SHADE1` | 50 % retractable shade screen | CC1 | Open/close, in MANUAL mode only |
| `HAF1` | HAF circulation fans along the central aisle | CC1 | On/off, in MANUAL mode only |
| `FOG1` | Foggers | CC1 | **Not installed in this phase.** Reserved ID; the app shows “Not available”. If added: 2, 3 or 5 min runs, blocked when RH > 85 %. |
| `CURT1` … | Roll-up curtain motors, if motorised | CC1 | **[OPEN-3]** |

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

Day-tanks are 50 L each, 6 in total (handoff). Each has a level sensor. `name` is what the app shows, so the zones can be labelled “Zone A” and “Zone B” while the IDs stay `Z1` and `Z2`.

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
├── simulator.json     drift rates, plant uptake, equipment effects, speed options
└── scenarios.json     “Set up your farm” presets and allowed ranges: regions, seasons, weather patterns,
                       crops and growth stages, starting water state, power and internet reliability,
                       saved scenarios, and the “Things to try” list for the playground panel
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
| `farm/{site}/zone/{zone}/event` | ZC → cloud | 1 | No | 6.5 Event |
| `farm/{site}/climate/event` | CC1 → cloud | 1 | No | 6.5 Event |
| `farm/{site}/zone/{zone}/cmd` | cloud → ZC | 1 | **Never** | 7.2–7.3 Dose, refill |
| `farm/{site}/climate/cmd` | cloud → CC1 | 1 | **Never** | 7.5–7.6 Climate mode, equipment |
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
| `ts` | When the reading was taken or the event happened (not when it was sent). |
| `device` | Who sent it. Must match the MQTT client ID (the broker ACL enforces this). |
| `bootId` | Increases by 1 every time the device restarts (the simulator increases it on a simulated reboot). |
| `seq` | Increases by 1 per message within a boot. `(bootId, seq)` gives exact order and shows gaps. |

Cloud → farm messages use the same envelope with `"device": "CLOUD"`.

---

## 6. Farm → cloud messages

### 6.1 Zone telemetry, `ZONE_TELEMETRY`

Sent every 60 s, and **immediately** when any value crosses an alarm threshold.

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
| `quality.*` | `OK` · `STALE` (no fresh sample) · `OUT_OF_RANGE` (outside sensor range) · `FAULT` (sensor error; value is `null`) · `UNCALIBRATED` |
| `mode` | `REMOTE` · `LOCAL` · `MAINT`, from the key switch on the panel. The cloud never sets it. |
| `equipment` | Pumps `ON`/`OFF`; valve `OPEN`/`CLOSED`. |
| `activeCmdId` | The command the controller is running now, or `null`. |
| `doMgL` | Dissolved oxygen; only when the zone's config says the sensor is fitted, otherwise `null` and the app hides that card. |

### 6.2 Climate telemetry, `CLIMATE_TELEMETRY`

Every 60 s, immediately on alarm.

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
  "detail": { "source": "ZC1 panel" }
}
```

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
| `reason` | A reason code (section 8.4) when the state is `PARTIAL`, `FAILED`, `STOPPED` or `REJECTED`. |
| `lines[].state` | `PENDING` · `RUNNING` · `DONE` · `SHORT` (delivered below tolerance) · `SKIPPED` |
| `after` | Measured after mixing. May arrive in a later `CMD_STATE` with the same final state. |

`EXECUTING` messages are sent when each line starts and ends, and at least every 5 s while a line runs (progress).

For refill and climate commands, `lines` is omitted, and `detail` carries what happened (for example `{ "fromPct": 41, "toPct": 90 }`).

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
- `limits` is a snapshot of the cloud's limits. The controller uses the **stricter** of these and its own config (7.7).
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

A running dose ends in `STOPPED` with the amounts delivered so far. As in the handoff, every role, Viewer included, may send a STOP; `ALL_EQUIPMENT` always asks for confirmation and warns that the house will heat up **[OPEN-8]**.

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
| `EXECUTING` | farm | Running. | Progress bars per line (done, running, queued), then step 4 “Mix and confirm” |
| `COMPLETED` | farm | All lines delivered within tolerance. | “Completed”, requested vs delivered, final EC |
| `PARTIAL` | farm | Some delivered, some short or skipped. | “Partial”, with the reason and amounts |
| `FAILED` | farm | Nothing delivered. | “Device error”, with the device and its last response |
| `STOPPED` | farm | Stopped by a STOP command, E-STOP or mode change; partial amounts recorded. | “Stopped by user”, who stopped it, partial amounts |
| `EXPIRED` | cloud | Not accepted before `expiresAt`. | “Not accepted in time — not sent again” |
| `UNKNOWN` | cloud | Farm went silent while the command was in flight; resolved by reconciliation. | “Result unknown — farm not responding” |

Final states: `REJECTED`, `COMPLETED`, `PARTIAL`, `FAILED`, `STOPPED`, `EXPIRED`. `UNKNOWN` is not final; it always resolves to one of the farm's final states, or stays `UNKNOWN` and is flagged for a person to check.

### 8.2 Allowed transitions

```
SUBMITTED → VALIDATING
VALIDATING → REJECTED | AWAITING_2ND_APPROVAL | APPROVED
AWAITING_2ND_APPROVAL → APPROVED | REJECTED (declined or timed out)
APPROVED → DISPATCHED
DISPATCHED → ACCEPTED | REJECTED (farm) | EXPIRED | UNKNOWN
ACCEPTED → EXECUTING | STOPPED | FAILED | UNKNOWN
EXECUTING → COMPLETED | PARTIAL | FAILED | STOPPED | UNKNOWN
UNKNOWN → COMPLETED | PARTIAL | FAILED | STOPPED | REJECTED
```

Any other transition is a bug. The back end refuses it and logs it.

### 8.3 Rules

1. **One command ID, one action.** The controller remembers the last 50 `cmdId`s. A repeat of a known ID gets its current state again and is **never executed twice**.
2. **Expiry.** The controller refuses a command whose `expiresAt` has passed, with `EXPIRED_ON_ARRIVAL` (reported as `REJECTED`). The cloud marks it `EXPIRED` if no `ACCEPTED` arrives in time.
3. **No automatic resend.** The cloud never re-sends an expired or unknown command. A person starts a new request.
4. **One operation per zone.** A dose or refill arriving while another runs in that zone is refused with `BUSY`.
5. **Journal first.** The controller writes the command to its journal *before* energising any pump.
6. **No replay after reboot.** After a restart, any journalled command still `ACCEPTED` or `EXECUTING` is reported as `PARTIAL` or `FAILED` with reason `REBOOT_MID_DOSE` and the measured amounts. It is never resumed.

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
| `EXPIRED_ON_ARRIVAL` | farm | Arrived after `expiresAt`. |
| `NO_FLOW` | farm | Pump ran but the flow meter saw nothing. |
| `FLOW_SHORT` | farm | Delivered below tolerance. |
| `TIMEOUT` | farm | `maxDurationS` reached. |
| `STOP_REQUESTED` | farm | A STOP command arrived. |
| `ESTOP_DURING` | farm | E-STOP pressed while running. |
| `MODE_CHANGED_DURING` | farm | Key switch left REMOTE while running. |
| `REBOOT_MID_DOSE` | farm | Controller restarted while running. |
| `NOT_MANUAL` | farm | Climate equipment command while climate is AUTOMATIC. |
| `NOT_INSTALLED` | farm | Equipment not installed (for example foggers). |
| `NEEDS_FANS` | farm | Pad pump switched on while fans are at stage 0. |
| `FEATURE_OFF` | cloud, farm | The feature is switched off for this site (for example climate Automatic or auto-refill in Phase A). |
| `CIRC_PROTECTED` | farm | Attempt to switch circulation (always refused). |
| `INVALID_COMMAND` | farm | Schema or value error. |

---

## 9. Controller rules (played by the simulator)

In Phase A the farm simulator plays every controller (`ZC1`, `ZC2`, `CC1`, `PM1`). These rules make it behave like one, so the back end, the app and the scenario tests see realistic behaviour.

1. Run its **own safety checks** on every command (E-STOP, REMOTE, BUSY, limits, 24 h totals from its own counters, day-tank and reservoir levels, fresh sensors) and refuse with a reason code (8.4). Never trust that the cloud checked.
2. Journal before acting; never replay after a reboot (8.3). The journal is kept in memory; a “reboot mid-dose” fault ends the dose as `PARTIAL` with `REBOOT_MID_DOSE`.
3. Keep the last good config in memory and keep working through a simulated internet drop.
4. Never switch circulation from a remote command.
5. Climate automation belongs on **CC1**; the cloud only sets the mode and manual overrides. In Phase A `climateAutomatic` is off, so CC1 acts only on manual commands and refuses `AUTOMATIC` with `FEATURE_OFF`.
6. Report every state change with `CMD_STATE` at QoS 1.
7. Message times come from the simulator’s clock, which may run fast in the playground (time rule, section 1).
8. Keep 24-hour totals per line from its own counters.

---

## 10. Gateway rules (played by the simulator)

`GW1` is played by the simulator. It covers what matters to the cloud, so the back end’s offline handling is finished and tested in Phase A: a **link drop** fault stops the farm’s messages, and on recovery the simulator sends `GATEWAY_JOURNAL` and the held-back messages in order.

1. **Relay without changing anything.** Same topics, same JSON.
2. **Hold farm → cloud messages** while a simulated link drop is on, oldest first.
3. **Never store or delay commands.** Cloud → farm messages are delivered immediately or not at all; the controller's expiry check is the backstop.
4. **On reconnect:** send `GATEWAY_JOURNAL` first, then replay the held messages in order.
5. Publish its own `DEVICE_STATUS` with `bufferedMessages` and `cloudLink: "UP" | "DOWN"`.

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
| HTTP codes | 200 OK · 201 created · 202 accepted (command started) · 400 bad input · 401 not signed in · 403 role · 404 · 409 conflict (BUSY, state) · 422 validation · 429 rate limit |
| Lists | `?limit=` (max 200) and `?cursor=`; response `{ "items": […], "nextCursor": "…" }`. |
| Time ranges | `?from=` and `?to=` in ISO-8601, or `?range=24h|7d|30d`. |

### 11.2 Endpoints

| Method and path | Purpose | Roles that may call it |
|---|---|---|
| `POST /auth/login` · `POST /auth/totp` · `POST /auth/logout` · `POST /auth/logout-all` | Sign in (with two-factor), sign out | Everyone |
| `GET /me` | Current user, role, CSRF token | Everyone signed in |
| `GET /farm` | Layout + equipment (section 2.4) | All |
| `GET /zones` · `GET /zones/{zoneId}` | Zone summaries and latest state | All |
| `GET /zones/{zoneId}/readings?metric=ec&range=24h` | Chart data (auto-bucketed) | All |
| `GET /zones/{zoneId}/operations` | Recent doses and refills | All |
| `POST /dose-estimates` `{ zone, lines }` | Estimated duration and projected EC while typing (called 300 ms after the last keystroke; no side effects) | Owner, Agronomist, Operator |
| `POST /dose-requests` | Run the checks; returns check results (11.3) | Owner, Agronomist, Operator |
| `POST /dose-requests/{id}/confirm` | Create and dispatch the command | Owner, Agronomist, Operator |
| `POST /dose-requests/{id}/approve` · `/decline` | Second approval (reserved; off in Phase 2A) | Owner |
| `GET /commands/{cmdId}` | Command with all states and lines | All |
| `POST /commands/{cmdId}/cancel` | Cancel before dispatch | Requester, Owner |
| `POST /zones/{zoneId}/refill` `{ "action": "START", "targetPct": 90 }` | Refill control | Owner, Agronomist, Operator |
| `PATCH /zones/{zoneId}/auto-refill` | Auto-refill settings | Owner, Agronomist |
| `POST /stop` `{ "scope": "ALL_OPERATIONS" }` | Stop (7.4) | **All roles, Viewer included** (handoff) |
| `GET /climate` | Climate state | All |
| `POST /climate/mode` `{ "mode": "MANUAL", "minutes": 30 }` | Climate mode | Owner, Agronomist, Operator |
| `POST /climate/equipment/{equipmentId}` `{ "action": "SET_STAGE", "value": 3 }` | Manual equipment control (7.6) | Owner, Agronomist, Operator |
| `GET /recipes` · `POST /recipes` · `POST /recipes/{id}/versions` · `POST /zones/{zoneId}/recipe` | Recipes | Read: all · write: Owner, Agronomist |
| `GET /limits` · `POST /limit-proposals` (reason required) · `POST /limit-proposals/{id}/decision` `{ "decision": "APPROVE" \| "REJECT" \| "REQUEST_CHANGES", "comment" }` | Safety limits; one pending proposal per limit per zone | Propose: Owner, Agronomist · decide: Owner |
| `GET /benches/{benchId}/cycles` · `POST /benches/{benchId}/cycles` · `PATCH /cycles/{id}` | Crop cycles | All except Viewer |
| `GET /inventory` · `POST /inventory/refills` | Day-tanks, use, run-out; record a hand refill | Read: all · record: all except Viewer |
| `GET /alerts?status=OPEN&severity=CRITICAL` · `POST /alerts/{id}/ack` | Alerts | Ack: all except Viewer |
| `GET /operations` · `GET /operations.csv` | Operation history and export | Read: all · export: all except Viewer |
| `GET /devices` · `GET /devices/{deviceId}` · `GET /devices/{deviceId}/diagnostics` | Devices | Diagnostics: Owner |
| `GET /power` | Power state | All |
| `GET /users` · `POST /users` · `PATCH /users/{id}` · `POST /users/{id}/invite` | Users and roles | Owner |
| `GET /health` | Uptime check | Public (no data) |

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
| `GET /sim/state` | Current scenario, simulated clock and date, speed, weather, active faults | All |
| `POST /sim/speed` `{ "x": 60 }` | Clock speed: 0 (pause), 1, 60 or 288 (one day in five minutes) | Owner, Agronomist, Operator |
| `POST /sim/faults` `{ "kind": "LEAK", "target": "Z1", "on": true }` | Switch a fault or event on or off (list below) | Owner, Agronomist, Operator |
| `POST /sim/reset` | Back to the start of the current scenario: all faults off, levels and readings reset | Owner |
| `GET /sim/scenario-options` | The presets and allowed ranges from `scenarios.json` for the “Set up your farm” screen, and the **“Things to try”** list (label, one-line hint, and the fault or screen it relates to) shown in the playground panel | All |
| `POST /sim/scenario` (body below) | Start a run from these starting conditions: resets the simulated farm, sets the clock and weather, plants the chosen crops | Owner, Agronomist, Operator |
| `GET /sim/scenarios` · `POST /sim/scenarios` · `DELETE /sim/scenarios/{id}` | List, save and delete named scenarios for repeat demos | Read: all · save/delete: Owner, Agronomist |

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
  "equipment": { "chillers": true, "shade": true, "fans": 4 }
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
| `equipment` | Within what the layout config allows | Shows what each piece of equipment is worth |

**Time on playground sites.** The simulator’s clock can run up to 288× faster. Message `ts` values are **simulated time**, so charts and history show the simulated day; the back end uses each message’s **arrival time** for staleness, command expiry and offline detection, and heartbeats are sent in real time. The app shows the simulated date and time next to the “Simulated farm” banner.

Starting conditions are **illustrative, not an agronomic forecast**: the app says so on the setup screen. Plant growth may show visually on the Farm view, but no yield numbers are produced. Events during a run still use `POST /sim/faults`.

Fault and event `kind`s: `EC_DRIFT_LOW` · `EC_DRIFT_HIGH` · `PH_DRIFT` · `SENSOR_FAULT` · `SENSOR_STALE` · `NO_FLOW` · `LEAK` · `RESERVOIR_LOW` · `DAYTANK_LOW` · `ESTOP` · `ZONE_MODE` (`value`: `LOCAL` or `MAINT`) · `HOT_AFTERNOON` · `CHILLER_FAULT` · `POWER_CUT` · `GENERATOR` · `UPS_LOW` · `LINK_DROP` · `DEVICE_OFFLINE` · `REBOOT_MID_DOSE`.

A fault only changes what the simulated farm does or reports. Everything the user then sees (blocked doses, alerts, history, reports) comes through the normal contract messages and back-end rules, exactly as it would on a real farm.

---

## 12. WebSocket (live updates to the app)

- URL: `wss://{host}/api/live`. Authenticated by the session cookie.
- The server sends only what the user's role may see.
- Server sends `{ "type": "PING" }` every 30 s; the client replies `{ "type": "PONG" }`. After a reconnect, the client refetches what it shows.

Every message: `{ "type": "…", "ts": "…", "data": { … } }`.

| `type` | `data` |
|---|---|
| `reading` | `{ "zone": "Z1", "readings": {…}, "quality": {…} }` or climate readings with `"zone": null` |
| `command_state` | `{ "cmdId", "kind", "zone", "state", "reason", "lines" }` |
| `alert` | `{ "id", "kind", "severity", "status", "zone", "message" }` |
| `device_state` | `{ "deviceId", "online", "lastSeen", "fw" }` |
| `power_state` | Same fields as 6.3 |
| `mode_change` | `{ "zone": "Z1", "mode": "MAINT" }` or `{ "climate": "MANUAL", "manualUntil": "…" }` |

---

## 13. Roles and permissions (summary)

| Action | Owner | Agronomist | Operator | Viewer |
|---|---|---|---|---|
| See everything | ✔ | ✔ | ✔ | ✔ |
| Stop all operations, Stop zone, Stop all equipment | ✔ | ✔ | ✔ | ✔ |
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
| Telemetry interval | 60 s (immediately on alarm) | ZC, CC1, PM1 |
| Heartbeat (`DEVICE_STATUS`) | 30 s | All devices |
| Device **stale** | 3 missed heartbeats (90 s) | Cloud |
| Device **offline** | 3 minutes without any message, or last will received | Cloud |
| Farm **offline** | 3 minutes with nothing from any device: the app's Live chip turns to Offline and every reading is treated as stale | Cloud and app |
| Fresh EC/pH to dose | ≤ 120 s old | Cloud and ZC |
| Command expiry | 60 s from `issuedAt` | Cloud sets, ZC enforces |
| `ACCEPTED` → `UNKNOWN` | 2 minutes with no `CMD_STATE` while the farm is silent | Cloud |
| Progress while dosing | at least every 5 s | ZC |
| Dose `maxDurationS` | 600 s (default) | ZC |
| Mix wait before `after` reading | 300 s (default) | ZC |
| Climate equipment confirmation | 30 s, then “No response from device” | App |
| Climate manual | 5–240 minutes | CC1 |

---

## 15. Open questions

| # | Question | Owner |
|---|---|---|
| OPEN-2 | Resolved by the handoff: chillers switch on/off and take a setpoint (18–28 °C) in Manual mode. | — |
| OPEN-3 | Are the roll-up curtains motorised? If so, add `CURT*` equipment. | Lead (vendor) |
| OPEN-4 | Is a pH-up line needed (`BASE`)? | Agronomy |
| OPEN-5 | Real numbers: 24 h limits, EC ceiling, pH bounds, EC/pH targets, minimum reservoir (placeholders until then; the 300 mL per-dose limit and 50 L day-tanks come from the handoff). | Lead + agronomy |
| OPEN-8 | The handoff lets every role, Viewer included, use all Stop buttons. Keep that for “Stop all equipment” too (it heats the house), or limit Viewers to Stop all operations and Stop zone? | Lead |
| OPEN-9 | 14 states, including `REJECTED`, `STOPPED` and the reserved `AWAITING_2ND_APPROVAL`. Confirm the “App shows” wording in 8.1. | Track 1 + Track 2 |
| OPEN-10 | Should STOP commands have a longer expiry than doses (a stop arriving late is usually still wanted, except `ALL_EQUIPMENT`)? | Track 1 + Track 3 |
| OPEN-11 | **Several clients later:** add an organisation layer (organisation → sites → zones). Proposed: every database table, API call and message carries `org_id` / `site_id` from day one, even with one value; users and data are kept separate per organisation. | Track 1 |
| OPEN-12 | **No fixed equipment assumptions:** the highest fan stage equals the number of fans in the layout config, and dosing lines come from `doseLines` in the config. No code, screen or check may assume exactly 4 fans or exactly A, B and ACID. | Tracks 1, 2, 3 |
| OPEN-14 | **Per-site feature switches:** a `features` block in each site’s config (for example `playground`, `demoFarm`, `climateAutomatic`, `autoRefill`, `secondApproval`, `foggers`; see 2.5), read by the API and the app, so each client can turn features on or off without code changes. | Tracks 1 + 2 |

---

## 16. Changing this contract

1. Changes only by **pull request** to `docs/contract.md`, together with the matching JSON Schema and example files in `docs/contract/schemas/` and `docs/contract/examples/`.
2. The pull request needs approval from Track 1, Track 3 and Track 4 (and Track 2 for sections 11–13).
3. **Additive** changes (a new optional field, a new event kind, a new reason code) keep `"v": 1`. Receivers already ignore unknown fields.
4. **Breaking** changes (renaming, removing or changing the meaning of a field) bump `"v"`. The back end accepts both versions until all devices are updated.
5. CI validates every example against its schema. The simulator and back end load the same schemas, so a mismatch fails the build.
6. Record every change in the changelog below.

---

## 17. Changelog

| Version | Date | Change | By |
|---|---|---|
| v0 | 22 Oct 2026 | First draft for the contract session, built from the tech stack, app and back-end guides and the revamp layout. | Lead |
| v0.1 | 22 Oct 2026 | Aligned with the Phase 2A Build Handoff: web app only, four roles, zone names A/B mapped to Z1/Z2, fan stage, shade, HAF fans, chiller setpoint, air temperature by position, dissolved oxygen, 3-minute offline, 5 % mismatch, 50 L day-tanks, Stop for every role, second approval off, dose estimate endpoint. | Lead |
| v0.2 | 22 Oct 2026 | Added scalability decisions OPEN-11 to OPEN-14 (organisation and site IDs, no fixed equipment counts, gateway device adapter, per-site feature switches); fan stage follows the number of fans. | Lead |
| v0.3 | 22 Oct 2026 | Re-phased: Phase A = web app, back end, cloud and farm simulator with playground; Phase B = firmware and gateway; Phase C = real farm. Added simulator control endpoints (11.4), what the simulator must implement (9) and its gateway emulation (10). | Lead |
| v0.4 | 22 Oct 2026 | Phase A is manual only: climate Automatic and auto-refill switched off by feature switches (`FEATURE_OFF`); Phase D added for automation (the handoff’s 2B); all farm values in per-client configuration packs (2.5, `config/clients/client_A/`). | Lead |
| v0.5 | 23 Oct 2026 | “Set up your farm” scenarios: starting conditions (region, month, time, weather, crops and stages, water state, raw water, power and internet reliability, equipment) via `POST /sim/scenario`, presets in `scenarios.json`, saved scenarios. Time rule for playground sites (simulated `ts`, arrival time for staleness and expiry). | Lead |
| v0.6 | 23 Oct 2026 | Dropped the auto-playing demo story from Phase A (`POST /sim/story` removed); added a “Things to try” list from `scenarios.json`; pause = speed 0. | Lead |
| v0.6.1 | 23 Oct 2026 | Phase A broker not exposed to the internet; Phase A alerts are in the app only. | Lead |
| v0.7 | 23 Oct 2026 | Phase A edition: every device is played by the simulator; sections 3, 9 and 10 describe only what the simulator does; broker internal only; later-phase items (firmware, real gateway, local mode, third-party hardware) moved out to `phase-2-later-phases.html`; OPEN-1, 6, 7 and 13 moved there too. | Lead |
