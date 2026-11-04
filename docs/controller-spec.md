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
| `EXECUTING` | E-STOP pressed | Outputs off, clear journal, `stopped`, report `STOPPED` `ESTOP_DURING` | Idle |
| `EXECUTING` | Key switch leaves REMOTE | Outputs off, clear journal, report `STOPPED` `MODE_CHANGED_DURING` | Idle |
| `EXECUTING` | `STOP` applies | Outputs off, clear journal, `stopped`, report `STOPPED` `STOP_REQUESTED` | Idle |
| Any | Reboot | See below | Idle |

A `REFILL` `START` does the same with the refill valve. Lines at 0 mL are `SKIPPED` and their pumps never run.

**Not built yet:** running a dose or refill to its end, which needs flow metering and pump rates (`hardware.json` is still empty), and `REFILL` `PAUSE`, `RESUME` and `STOP`, which are refused with `INVALID_COMMAND` until then. Until execution exists, delivered amounts stay 0.

`STOP`, `RESUME` and `CLEAR_FAULT` have no run phase: `ACCEPTED`, act, `COMPLETED`. A `ZONE` stop for another zone gets no answer at all.

## A reboot

`restart(reason)` runs on the new boot. RAM is gone; `nvs` is not.

1. All outputs off, and circulation on. The controller starts circulation itself; no command can.
2. `stopped` is true until a person sends `RESUME`.
3. The `cmdId` memory, latches and 24 h totals come back from `nvs`.
4. A command still in the journal is reported `PARTIAL` if anything was delivered, otherwise `FAILED`, with `REBOOT_MID_DOSE`. It is never resumed.
5. A `REBOOTED` event gives the reason.

A new farm is not a reboot: it starts with `stopped` false.

| `nvs` key | Holds | Written |
|---|---|---|
| `journal` | The running command | Before any output switches on, at each step; emptied when it ends |
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
| Clocks | `now_ms()`: the controller's clock, simulated on playground sites, for 24 h totals. `utc_ms()`: real time, for expiry |
