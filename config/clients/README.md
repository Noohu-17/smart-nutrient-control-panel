# Client configuration packs

Every farm-specific value lives here: layout, equipment, limits, targets, alert thresholds, setpoint ranges, timings, feature switches and simulator settings. The back end, the app and the simulator all read these files. See `docs/contract.md` §0 ("No hard-coded values") and §2.5.

**Change a client by changing files, never code.** If a value is fixed in code, that is a bug: move it into the client's pack.

## One folder per client

```
config/clients/
├── client_A/      first demo pack (SIET Polyhouse 1)
└── client_B/      the next client, same 11 files
```

Each folder has the 11 files listed in contract §2.5: `layout.json`, `limits.json`, `recipes.json`, `alerts.json`, `climate.json`, `features.json`, `users.json`, `simulator.json`, `hardware.json`, `io_map.json` and `scenarios.json`.

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
  | `simulator.json` | `speedOptions`, `model.numerics` (solver settings) | everything else under `model` (the farm model's rates, T3-2); each value's source is in `_meta.sources` |

  Every other file except `layout.json` and `features.json` is placeholder throughout.

Before loading a pack for a real farm, every `"placeholder": true` must be gone.

Some other conventions:

- A value that follows another file is a reference, not a copy. `"maxStageFrom": "layout.climate.fans"` means the top fan stage is the number of fans in the layout. Never write 4.
- `users.json` holds demo users only. Passwords and hashes never go in git: `"_meta.passwordFrom": "env"` means the back end reads them from its environment.
- Names follow contract §1: `lowerCamelCase` keys, `UPPER_SNAKE_CASE` enum values, and units in the key name (`solTempC`, `usableL`), never in the value.

## Validating

```
python scripts/validate_config.py
```

The validator needs Python 3 and no extra packages. It parses every `config/clients/*/*.json` (duplicate keys count as an error) and checks:

- every client has all 11 files;
- `layout.json`: `v` is 1; zone IDs are unique and match `Z{n}`; bench IDs match `{zone}-B{n}`; each zone's controller is a `ZONE_CONTROLLER` in `devices`; `doseLines` match the `dayTanks` keys; chillers are keyed by zone IDs that exist; the climate controller and power monitor are in `devices`; client_A lists exactly `GW1`, `ZC1`, `ZC2`, `CC1` and `PM1`;
- every other file except `features.json` has `"v": 1` and `_meta.placeholder`;
- `limits.json` and `alerts.json` are keyed by the layout's zone IDs, and the per-line limits are keyed by that zone's `doseLines`.

It exits non-zero on any problem and prints one `FAIL` line for each. To check a folder outside the repo, pass it as an argument: `python scripts/validate_config.py path/to/clients`.

JSON Schema validation will replace most of these checks once the contract schemas exist (`docs/contract/schemas/`, contract §16).
