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

## Real values and placeholders

JSON has no comments, so placeholder status is written into the files:

- **Real values with no marker:** `layout.json` (contract §2.4) and `features.json` (contract §2.5).
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

- **What the dose review reads (T1-16):** `recipes.json` `zones` is the crop and stage each zone is on, so its EC/pH target, until recipes can be applied from the app (`POST /zones/{zoneId}/recipe`). `hardware.json` `pumps.doseLines.*.flowMlPerS`, the simulator's pump rates (T4-8), gives the estimated dose time. The validator checks that every zone has a recipe that exists.

Before loading a pack for a real farm, every `"placeholder": true` must be gone.

## client_B

client_B is the farm with the prototype-extension rig (T1-31), from the farm details form the team filled in (R-1). Its `_meta` notes say which values are the form's and which are proposed for the team to check. In short:

- **Two NFT zones**, `Z1` and `Z2` ("NFT Zone A" and "B"): 4 NFT structures each (3 channels, 2.1 m, 45 plant sites), each with its own 90 L main tank, and lettuce and spinach at the seedling stage. The low level is 20 % of the main tank, 18 L.
- **Dose lines `N`, `P` and `K`**, each from a 10 L tank (confirmed at the farm). The form's phosphoric acid has no pump (IN4 is the water inlet on both controllers), so it isn't a dose line.
- **Zone A (`ZC1`) runs on the rig** (`io_map.json`): the actuator box, N, P and K on relay1–relay3, TDS at the 0.5 factor, no drum level sensors. Its pumps are calibrated at 15 mL/min (`hardware.json`).
- **Zone B (`ZC2`) joins the rig with per-zone pump rates** (T1-31's pump-rate PR): `hardware.json` has one rate per dose line, and Zone B's pumps run 1 L/min.
- **Not fitted:** any climate equipment (`CC1`) and a power monitor (`PM1`). The layout format needs them, so they are listed with nothing on them, and the app shows them as not available.
- **No playground** (`features.json`) and no simulator model: the simulator refuses to start with this pack, so it can never play the rig's zones.
- **Proposed in `layout.json`**, which has no `_meta`: the site ID `coimbatore-ph1`, the zone names' capital letters, `nursery.tables` 0, and `power` `generator` and `ups` false (the form's UPS answer is "?").

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
- `limits.json` and `alerts.json` are keyed by the layout's zone IDs, and the per-line limits are keyed by that zone's `doseLines`.

It exits non-zero on any problem and prints one `FAIL` line for each. To check a folder outside the repo, pass it as an argument: `python scripts/validate_config.py path/to/clients`.

JSON Schema validation will replace most of these checks once the contract schemas exist (`docs/contract/schemas/`, contract §16).
