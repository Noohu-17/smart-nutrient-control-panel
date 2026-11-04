# Controller test vectors

Each `*.json` file here is one controller rule written as data: a starting state, a timeline of
inputs and commands, and what the controller must and must never do. The Python controller plays
them through `tests/rules/vector_runner.py`, and the C++ firmware will run the same files later
(contract §9, simulator guide §9).

**Status:** `tests/rules/test_vectors.py` plays every file here against `sim/controller/`, and
`tests/rules/test_vector_files.py` checks their structure and contract consistency. Whether a vector
passes comes from the runner's output, never from the file. See [TODO.md](TODO.md) for every rule
still to be written.

## How a runner plays a vector

1. Build a scripted HAL from `start`: `inputs` answer `read_input`, `sensors` answer `read_sensor`
   with `(value, status)`, `outputs` are the current output states, `nvs` is the non-volatile store.
   The controller begins with `start.config` (its last good `DEVICE_CONFIG`, §7.7),
   `start.dayTanks` (as in `layout.json`) and `start.controller`. There, `dosed24hMl` counts as
   delivered at time 0, `doseHistory` lists `[atMs, line, ml]` with `atMs` negative for earlier, and
   `recentCmdIds` lists `{ cmdId, state, reason }` for each command already remembered.
2. The controller's real-time clock reads `start.wallClockUtc` + `now_ms`. Expiry (§8.3, §14) is
   judged on this clock.
3. Advance `now_ms` from 0 to `runUntilMs` in steps of `stepPeriodMs`. Before the step at a timeline
   entry's `atMs`, apply its action, then call `step(now_ms)`:
   - `SET_INPUTS`: change the listed HAL inputs.
   - `SET_SENSORS`: change the listed sensors (`{ "EC": { "value": 1.4, "status": "OK" } }`).
   - `DELIVER_COMMAND`: call `handle(message)` with the full contract message, as received on `topic`.
     `"malformed": true` says the message breaks the contract on purpose, so the structure checks
     leave its body alone.
   - `REBOOT`: every output drops, RAM is lost and `nvs` stays. A new controller calls
     `restart(reason)`, and the next messages carry the next `bootId` with `seq` starting again.
4. Record every `set_output` call, every `nvs_write`, and every message the controller reports.

Later vectors may need more actions (`FLOW_PULSES`, `ADVANCE_TO`). Add them here first.

## How results are compared

| Field | Meaning |
|---|---|
| `timeline[].expect.messages[]` | Messages the controller must report between this entry's `atMs` and the next entry's (or `runUntilMs`). |
| `match` | Subset match. Every listed field must equal the reported one. Objects are compared recursively; other fields may be present. `null` must be `null`. |
| `nonEmpty` | These fields must be present and be non-empty strings (for example an `EVENT`'s `message`, `systemAction`, `nextStep`, §6.5). |
| `count` | Exactly this many matching messages in the window. Without it, at least one. |
| `timeline[].expect.calls` | HAL calls that must happen in the window, in this order, others may come between: `nvs_write:<key>`, `set_output:<id>:on` or `:off`. |
| `topic` | The contract §4 topic the message goes to. |
| `never.outputsOn` / `never.outputsOff` | These outputs must never be switched on / off at any time in the run. |
| `never.messages[]` | No reported message may match any of these. |
| `final.outputs` | Output states at `runUntilMs`. |
| `final.lastZoneState` | Subset match against the last `ZONE_STATE` reported. |

Envelope fields (`msgId`, `ts`, `bootId`, `seq`) are not compared by value, unless a `match` lists
one. In every vector the runner also checks the full §5 envelope, `(bootId, seq)` always going up,
every `ZONE_STATE` against Track 1's model, the three answers in every `EVENT`, and that a
`CMD_STATE` has a `reason` only where §6.6 gives one.

## Writing a vector

- Field names and enum values follow contract §1 (`lowerCamelCase`, `UPPER_SNAKE_CASE`).
- Commands are full contract messages (§7.1 envelope plus the kind's body). `cmdId` and `msgId` are
  UUID v4, newly generated for each vector. Never reuse the contract's example IDs.
- Set the command's `ts`/`issuedAt` at or before its arrival time and `expiresAt` 60 s after
  `issuedAt`, or 5 minutes for a `STOP` other than `ALL_EQUIPMENT` (§7.4, §14), unless expiry is what
  the vector tests.
- Make every check pass except the one under test, and say so in `assumptions`.
- List the assumptions a reviewer could disagree with. If a vector depends on an unsettled rule,
  name the open decision.
- Dosing lines come from `start.doseLines`. Don't assume A, B and ACID (OPEN-12).
