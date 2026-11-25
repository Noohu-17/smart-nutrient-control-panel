# Client configuration packs

Every farm-specific value lives here: layout, equipment, limits, targets, alert thresholds, setpoint ranges, timings, feature switches and simulator settings. The back end, the app and the simulator all read these files. See `docs/contract.md` §0 ("No hard-coded values") and §2.5.

**Change a client by changing files, never code.** If a value is fixed in code, that is a bug: move it into the client's pack.

## One folder per client

```
config/clients/
├── client_A/      first demo pack (SIET Polyhouse 1)
└── client_B/      the prototype-extension rig farm (Hydroponic polyhouse, Coimbatore), same files
```

Each folder has the 12 files listed in contract §2.5: `layout.json`, `limits.json`, `recipes.json`, `nutrients.json`, `alerts.json`, `climate.json`, `features.json`, `users.json`, `simulator.json`, `hardware.json`, `io_map.json` and `scenarios.json`.

To add a client, copy an existing folder, change the values, run the validator, then load it. Collect their layout, equipment, crops and targets, limits and alert contacts first (contract §2.5, "New client").

**Loading it** means importing it into the database, where a server reads it from (T1-39): `python -m app.db.config_store import ../config/clients/<client>` from `backend/`. The import runs the validator's checks again and refuses the pack if any fails; each change is a new version, and any version exports back to a folder (`backend/README.md`, "Client settings in the database"). These folders stay where settings are written and reviewed.

## Real values and placeholders

JSON has no comments, so placeholder status is written into the files:

- **Real values with no marker:** `layout.json` (contract §2.4) and `features.json` (contract §2.5).
- **Live or simulated:** each zone in `layout.json` has a `source`, `LIVE` (real hardware runs it) or `SIMULATED` (the simulator plays it). Left out, it is `SIMULATED` on a playground site and `LIVE` everywhere else (contract §2.4). client_A's zones are `SIMULATED` and client_B's are `LIVE`. The validator checks the value, and that a zone on the prototype rig is `LIVE`.
- **Every other file** starts with `"v": 1` and a `_meta` block:

  ```json
  "_meta": { "client": "client_A", "placeholder": true, "note": "Placeholder values until <source>. Do not use for a real farm." }
  ```

  `note` says what the real numbers are waiting for (for example agronomy, OPEN-5).
- **Files that mix real and placeholder values** also list the placeholder keys in `_meta.placeholderKeys`, as dotted paths (`*` stands for any zone ID). In client_A:

  | File | Real | Placeholder |
  | :--- | :--- | :--- |
  | `limits.json` | `limits.maxPerDoseMl.A` and `.B` (300 mL each, from the handoff) | everything else, including `limits.maxPerDoseMl.ACID` (OPEN-5) |
  | `climate.json` | fan stages, chiller setpoint range, manual minutes, fogger rules | `setpoints.airTempMaxC`, `setpoints.airRhMax`, `setpoints.solTempMaxC` |
  | `simulator.json` | `speedOptions`, `minRealReadingIntervalS`, `model.numerics` (solver settings) | everything else under `model` (the farm model's rates, T3-2), and `demoStart` (the playground's starting state, where `POST /reset` returns, T4-9); each value's source is in `_meta.sources` |

  Every other file except `layout.json` and `features.json` is placeholder throughout.

- **What a zone, crop and dose line are (T1-38):** each `layout.json` zone has a `growingSystem` (`NFT`, `DWC`, `DUTCH_BUCKET`, `DRIP`, `VERTICAL` or `MIXED`), `growingUnits` (one entry per kind of unit: `type`, `count`, `plantSitesEach` and the size its type needs; `NFT_BENCH` units are the zone's `benches`), a `size` (`lengthM`, `widthM`), a `position` (`xM`, `yM` from the site's corner and a `label`), `notes`, a `reservoir.volumeL` beside `usableL` (the tank), and `sensors` saying `true` or `false` for every sensor: `ec`, `ph`, `solTemp`, `reservoirLevel`, `circFlow`, `dayTankLevel`, `leak` and `dissolvedOxygen`. The controller's link is `io_map.json`'s (`MQTT` unless it says `SF_GATEWAY`). `recipes.json` `zones` gives each zone's `crop`, `variety`, `stage` and `plantedOn` (`YYYY-MM-DD`). `nutrients.json` gives each dose line's `name`, `compound`, `strengthPct`, `drum` (`id`, `volumeL`) and `purpose`; its pump rate is `hardware.json`'s, never copied. `GET /api/zones` answers these as each zone's `profile` and `crop`, and `GET /api/zones/{zoneId}/dose-limits` each line's stock and `pumpMlPerMin` (contract 2.4, 11.2).
- **Proposed layout values in client_A (T1-38)**, which has no `_meta` in `layout.json`: `reservoir.volumeL` (1300 L), `plantSitesEach` (252: 6 channels of 42 sites), the zone sizes and positions, and `sensors.leak`. Its `nutrients.json` names, compounds, strengths and drums are placeholders like the rest of that file.
- **What the dose review reads (T1-16):** `recipes.json` `zones` is the crop and stage each zone is on, so its EC/pH target, until recipes can be applied from the app (`POST /zones/{zoneId}/recipe`). `hardware.json` `pumps.doseLines.*.flowMlPerS`, the simulator's pump rates (T4-8), gives the estimated dose time; a zone whose pumps run at their own rate has them under `pumps.zones.<zone>.doseLines` (T1-31), and its other lines keep the shared rate. The validator checks that every zone has a recipe that exists.

Before loading a pack for a real farm, every `"placeholder": true` must be gone.

## client_B

client_B is the farm with the prototype-extension rig (T1-31), from the farm details form the team filled in (R-1). Its `_meta` notes say which values are the form's and which are proposed for the team to check. In short:

- **Two NFT zones**, `Z1` and `Z2` ("NFT Zone A" and "B"): 4 NFT structures each (3 channels, 2.1 m, 45 plant sites), each with its own 90 L main tank, and lettuce and spinach at the seedling stage. The low level is 20 % of the main tank, 18 L.
- **Dose lines `N`, `P` and `K`**, each from a 10 L tank (confirmed at the farm). The form's phosphoric acid has no pump (IN4 is the water inlet on both controllers), so it isn't a dose line.
- **Zone A (`ZC1`) runs on the rig's Zone 1** (`io_map.json`): the actuator box, stocks A, B and C on relay1–relay3, TDS at the 0.5 factor, no drum level sensors. Its pumps are calibrated at 15 mL/min (`hardware.json` `pumps.doseLines`).
- **Zone B (`ZC2`) runs on the rig's Zone 2**: the gateway's own relay board, stocks A, B and C on relay1–relay3 (IN4 is the solenoid valve that fills its tank), TDS at the 0.5 factor, no drum level sensors. Its pumps run 1 L/min, the gateway's setting, not yet timed at the farm (`hardware.json` `pumps.zones.Z2`). At that rate 1 mL is 0.06 s, under the rig's shortest dose (0.1 s), so Zone B's doses under 2 mL won't run.
- **Not fitted:** any climate equipment (`CC1`) and a power monitor (`PM1`). The layout format needs them, so they are listed with nothing on them, and the app shows them as not available.
- **No playground** (`features.json`) and no simulator model: the simulator refuses to start with this pack, so it can never play the rig's zones.
- **Proposed in `layout.json`**, which has no `_meta`: the site ID `coimbatore-ph1`, the zone names' capital letters, `nursery.tables` 0, and `power` `generator` and `ups` false (the form's UPS answer is "?"); and from T1-38, the zone sizes (3 x 4 m) and positions. The NFT structures (3 channels, 2.1 m, 45 plant sites) and the sensors fitted are the form's and the rig's: TDS (as EC), pH, water temperature and tank level on Zones A and B; no circulation flow, leak, stock tank level or DO sensor.
- **Crops and stocks (T1-38, DM-5):** the varieties (Grand Rapids, Ooty 1) are the form's. The dose lines are the team's three stocks, A, B and C: City Greens Grow (NPK 24-00-00, nitrogen and calcium), Flora (05-26-00, phosphorus) and Thrive (06-00-19, potassium and magnesium), each with micronutrients; `strengthPct` is the main nutrient's % in 100 g, and each line's `purpose` is the team's. There is no pH-down line. The stock tank size (`drum.volumeL`, 10 L) is confirmed by Noohu. The planting date, 2026-11-10 for both zones, is proposed, and each stock's EC and pH change per mL waits for a jar test (`_meta.placeholderKeys`).

Some other conventions:

- A value that follows another file is a reference, not a copy. `"maxStageFrom": "layout.climate.fans"` means the top fan stage is the number of fans in the layout. Never write 4.
- `users.json` holds demo users only. Passwords and hashes never go in git: `"_meta.passwordFrom": "env"` means the back end reads them from its environment.
- Names follow contract §1: `lowerCamelCase` keys, `UPPER_SNAKE_CASE` enum values, and units in the key name (`solTempC`, `usableL`), never in the value.

## Validating

```
python scripts/validate_config.py
```

The validator needs Python 3 and no extra packages. It parses every `config/clients/*/*.json` (duplicate keys count as an error) and checks:

- every client has all 12 files;
- `layout.json`: `v` is 1; zone IDs are unique and match `Z{n}`; bench IDs match `{zone}-B{n}`; each zone's controller is a `ZONE_CONTROLLER` in `devices`; `doseLines` match the `dayTanks` keys; chillers are keyed by zone IDs that exist; the climate controller and power monitor are in `devices`; client_A lists exactly `GW1`, `ZC1`, `ZC2`, `CC1` and `PM1`;
- every other file except `features.json` has `"v": 1` and `_meta.placeholder`;
- `limits.json` and `alerts.json` are keyed by the layout's zone IDs, and the per-line limits are keyed by that zone's `doseLines`;
- each zone's `limits.json` values make sense (DM-9): `maxPerDoseMl` and `max24hMl` above 0, and per-dose no more than that line's 24 h limit; `ecCeiling` above 0; `phMin` and `phMax` within 0–14, with `phMin` below `phMax`; `minReservoirPctToDose` within 0–100; `dose.mixWaitS` and `dose.maxDurationS` not negative;
- each zone's details (T1-38): a known `growingSystem`; `growingUnits` of known types with a whole `count` and `plantSitesEach` above 0, the size keys their type needs, and a type that belongs to the zone's system; as many `NFT_BENCH` units as `benches`, each bench the size of one; `size`, `position` and `notes`; `reservoir.volumeL` no less than `usableL`; and `sensors` with exactly the eight sensor keys, each `true` or `false`;
- each zone's crop in `recipes.json`: a `variety` and a real `plantedOn` date (`YYYY-MM-DD`);
- each `nutrients.json` dose line: `name`, `compound` and `purpose` as text, `strengthPct` above 0 and no more than 100, and a `drum` with an `id` no other line uses and a `volumeL` no smaller than any day-tank it fills;
- every dose line of every zone has a pump rate above 0: the zone's own `hardware.json` `pumps.zones.<zone>.doseLines.<line>.flowMlPerS` if it has one, else `pumps.doseLines.<line>.flowMlPerS`; `pumps.zones` is keyed by layout zones, and each one's `doseLines` by that zone's dose lines;
- a controller on the prototype rig (`"link": "SF_GATEWAY"` in `io_map.json`) runs a `LIVE` zone, and each line's largest dose (`limits.json` `maxPerDoseMl`) fits in the rig's 600 s at that zone's pump rate.

It exits non-zero on any problem and prints one `FAIL` line for each. To check a folder outside the repo, pass it as an argument: `python scripts/validate_config.py path/to/clients`.

JSON Schema validation will replace most of these checks once the contract schemas exist (`docs/contract/schemas/`, contract §16).
