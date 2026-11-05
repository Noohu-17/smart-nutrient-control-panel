# Controller spec

How the zone controller in `simulator/sim/controller/` behaves, written once for Python and the later C++ firmware. Contract §6.6, §7–§9 is the agreement: if this file and the contract disagree, the contract is right. The rules are tested as data in `simulator/tests/vectors/`.

## Safety checks

`checks.refusal()` runs these in order and reports the first that fails. A repeat of a known `cmdId` comes before all of them: it gets its current state again and is never run twice.

| # | Reason | Fails when | DOSE | REFILL `START` | RESUME | STOP, CLEAR_FAULT |
|---|---|---|---|---|---|---|
| 1 | `CIRC_PROTECTED` | The command targets `{zone}-CIRC` | ✔ | ✔ | ✔ | ✔ |
| 2 | `INVALID_COMMAND` | A schema or value error | ✔ | ✔ | ✔ | ✔ |
| 3 | `EXPIRED_ON_ARRIVAL` | It arrives at or after `expiresAt` | ✔ | ✔ | ✔ | ✔ |
| 4 | `ESTOP` | The E-STOP is pressed | ✔ | ✔ | ✔ | |
| 5 | `FAULT_LATCHED` | A latch is active | ✔ | ✔ | ✔ | |
| 6 | `ZONE_STOPPED` | The zone is stopped | ✔ | ✔ | | |
| 7 | `NOT_REMOTE` | The key switch isn't on REMOTE | ✔ | ✔ | | |
| 8 | `BUSY` | A dose or refill is running | ✔ | ✔ | | |
| 9 | `SENSOR_STALE` | EC or pH is null or not `OK` | ✔ | | | |
| 10 | `LIMIT_DOSE` | A line is over its per-dose maximum | ✔ | | | |
| 11 | `LIMIT_24H` | Its rolling 24 h total would pass the maximum | ✔ | | | |
| 12 | `DAYTANK_LOW` | Its day-tank holds less than requested | ✔ | | | |
| 13 | `RESERVOIR_LOW` | The reservoir is below the minimum to dose | ✔ | | | |

Limits are the stricter of the command's snapshot and the controller's own (§7.2). A value exactly at a limit passes.

These are choices the contract leaves open:

- **`CIRC_PROTECTED` first.** §9 rule 1 doesn't list it. It comes first, as a command aimed at circulation would otherwise be refused as invalid.
- **Nothing known means refused.** A level sensor that isn't `OK` gives `DAYTANK_LOW` or `RESERVOIR_LOW`, and a line or minimum with no limit in either source gives `LIMIT_DOSE`, `LIMIT_24H` or `RESERVOIR_LOW`, because none of these can be shown safe. Only EC and pH have `SENSOR_STALE` (§8.4).
- **Freshness comes through the status.** The sensor layer marks an EC or pH older than 120 s (§14) as `STALE`, and the check reads that status.
- **A dose of nothing is invalid.** A `DOSE` whose lines are all 0 mL is an `INVALID_COMMAND`.
- **Clearing a fault that isn't latched** answers `COMPLETED`: there is nothing left to clear.
- **`target.equipment`** is how a command would name circulation. No contract command does.

## Operations

One operation per zone. A dose moves through these, reporting `CMD_STATE` and `ZONE_STATE` at each step:

| From | Event | Does | To |
|---|---|---|---|
| Idle | `DOSE` passes every check | Journal, report `ACCEPTED` | `ACCEPTED` |
| `ACCEPTED` | Straight away | Read `before`, journal, first line's pump on, report `EXECUTING` | `EXECUTING` |
| `EXECUTING` | The line's flow meter reaches its request | Pump off, record the measured mL, next line's pump on, report `EXECUTING` | `EXECUTING` |
| `EXECUTING` | Every 5 s of real time on a line | Journal the measured mL, report `EXECUTING` | `EXECUTING` |
| `EXECUTING` | The last line reaches its request | Pump off, clear journal, report `COMPLETED` (`PARTIAL` `FLOW_SHORT` if a line was short) with `after` null | Mix wait |
| `EXECUTING` | `maxDurationS` passes | Pump off, clear journal, report `PARTIAL` `TIMEOUT`, or `FAILED` `TIMEOUT` if nothing was delivered | Mix wait, or Idle |
| Mix wait | `mixWaitS` passes | Read `after`, report the same final state again with it | Idle |
| Mix wait | STOP, E-STOP or key switch | Nothing more: the final state stands and `after` stays null (§6.6) | Idle |
| `EXECUTING` | E-STOP pressed | Outputs off, clear journal, `stopped`, report `STOPPED` `ESTOP_DURING` | Idle |
| `EXECUTING` | Key switch leaves REMOTE | Outputs off, clear journal, report `STOPPED` `MODE_CHANGED_DURING` | Idle |
| `EXECUTING` | `STOP` applies | Outputs off, clear journal, `stopped`, report `STOPPED` `STOP_REQUESTED` | Idle |
| Any | Reboot | See below | Idle |

Lines run one at a time, in `order`. Lines at 0 mL are `SKIPPED` and their pumps never run. A line is `DONE` when its meter has counted at least `(100 − tolerancePct)%` of the request, otherwise `SHORT`. `maxDurationS`, `mixWaitS` and `tolerancePct` come from the command, or else from `limits.json` `dose`, or else from §14's defaults.

A `REFILL` `START` opens the valve and closes it when the reservoir reaches `targetPct`, reporting `COMPLETED` with `detail: { fromPct, toPct }`. At `maxDurationS` it closes with `PARTIAL` `TIMEOUT` if the level rose, otherwise `FAILED` `TIMEOUT`. It has no mix wait.

**Clocks.** Pumps, `maxDurationS` and the mix wait run on the controller's clock, which is simulated time in the simulator. Progress runs on real time, because it is for people watching: at ×288 a whole line can take less than a real second.

**Only measured mL count.** The 24 h totals and every `deliveredMl` come from the flow meters, never from the request.

**Not built yet:** `REFILL` `PAUSE`, `RESUME` and `STOP`, which are refused with `INVALID_COMMAND`; `NO_FLOW`, which needs a "pump on, no flow" time in `hardware.json`. Until then a pump that delivers nothing ends at `maxDurationS`.

`STOP`, `RESUME` and `CLEAR_FAULT` have no run phase: `ACCEPTED`, act, `COMPLETED`. A `ZONE` stop for another zone gets no answer at all.

## A reboot

`restart(reason)` runs on the new boot. RAM is gone; `nvs` is not.

1. All outputs off, and circulation on. The controller starts circulation itself; no command can.
2. `stopped` is true until a person sends `RESUME`.
3. The `cmdId` memory, latches and 24 h totals come back from `nvs`.
4. A command still in the journal is reported `PARTIAL` if anything was delivered, otherwise `FAILED`, with `REBOOT_MID_DOSE`. It is never resumed.
5. A `REBOOTED` event gives the reason.

A new run of the simulator is not a reboot (agreed on #31). `start()` brings back what `nvs` kept and closes a journalled command the same way, without a `REBOOTED` event, and the zone is not stopped.

| `nvs` key | Holds | Written |
|---|---|---|
| `journal` | The running command, with the mL measured so far | Before any output switches on, at each line and progress report; emptied when it ends |
| `recentCmds` | The last 50 `cmdId`s with their last `CMD_STATE` | At every state |
| `latches` | Active latches | When one is added or cleared |
| `doseHistory` | `[atMs, line, ml]` from the last 24 h | When mL are delivered |

## HAL names

Provisional until `io_map.json` is agreed (§2.5).

| Kind | Names |
|---|---|
| Inputs | `ESTOP`, `MODE_REMOTE`, `MODE_MAINT` (neither means LOCAL), `LEAK` (not read yet) |
| Outputs | `PUMP_<line>` for each entry in `doseLines`, `REFILL_VALVE`, `CIRC_PUMP` |
| Sensors | `EC`, `PH`, `RESERVOIR_PCT`, `DAYTANK_<line>_PCT` |
| Flow meters | `FLOW_<line>`: `take_flow_ml()` gives the mL counted since the last call. Real firmware counts pulses and converts them with the meter's calibration |
| Clocks | `now_ms()`: the controller's clock, simulated on playground sites, for pumps, timeouts, the mix wait and 24 h totals. `utc_ms()`: real time, for expiry and progress |

In the simulator the HAL is `sim/farm_hal.py`, over the zone's part of the farm model. Its `nvs` is `simulator/.state/nvs_<device>.json`.
