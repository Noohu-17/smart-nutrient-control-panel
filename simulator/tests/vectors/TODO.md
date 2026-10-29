# Controller test vectors: catalogue

Every controller rule from contract v0.8 §7–§9, one future vector per line. §7–§9 are still a
**draft** (the contract-lock review covered §1–§6.4 only), so expect edits after the contract session.

**Status values:** `WRITTEN`: the file exists and passes the structural checks, but has **not been
run** against a controller (no controller or runner exists yet). `TODO`: not written. No vector is
passing yet.

**Who checks:** `ZC` zone controller · `CC1` climate controller · both are played by the simulator
and later by firmware. Cloud-only checks are listed separately at the end. They are not controller
vectors.

## 1. Own safety checks on a start command (§9 rule 1, §8.4)

| ID | File | Device | Rule, in one line | Contract | Status |
|---|---|---|---|---|---|
| ZC-DOSE-ESTOP-01 | `dose_refused_estop.json` | ZC | DOSE while the E-STOP is pressed → `REJECTED` `ESTOP`; no pump runs, circulation stays on | §9.1, §8.4 | WRITTEN |
| ZC-DOSE-NOT-REMOTE-01 | `dose_refused_local.json` | ZC | DOSE with the key switch on LOCAL → `REJECTED` `NOT_REMOTE` | §9.1, §8.4, §0 | TODO |
| ZC-DOSE-NOT-REMOTE-02 | `dose_refused_maint.json` | ZC | DOSE with the key switch on MAINT → `REJECTED` `NOT_REMOTE` | §9.1, §8.4 | TODO |
| ZC-DOSE-BUSY-01 | `dose_refused_busy_dose.json` | ZC | DOSE while another dose runs → `REJECTED` `BUSY`; the running dose is unaffected | §8.3.4 | TODO |
| ZC-DOSE-BUSY-02 | `dose_refused_busy_refill.json` | ZC | DOSE while a refill runs → `REJECTED` `BUSY` | §8.3.4 | TODO |
| ZC-REFILL-BUSY-01 | `refill_refused_busy_dose.json` | ZC | REFILL START while a dose runs → `REJECTED` `BUSY` | §8.3.4 | TODO |
| ZC-DOSE-LIMIT-01 | `dose_refused_per_dose_limit.json` | ZC | One line over `maxPerDoseMl` → `REJECTED` `LIMIT_DOSE`; no line runs at all | §7.7, §8.4 | TODO |
| ZC-DOSE-LIMIT-02 | `dose_limit_device_stricter.json` | ZC | Command snapshot looser than device config → the device's stricter limit applies | §7.2 | TODO |
| ZC-DOSE-LIMIT-03 | `dose_limit_command_stricter.json` | ZC | Device config looser than command snapshot → the command's stricter limit applies | §7.2 | TODO |
| ZC-DOSE-24H-01 | `dose_refused_24h_total.json` | ZC | Own 24 h counter + request > `max24hMl` → `REJECTED` `LIMIT_24H` | §9.1, §9.8 | TODO |
| ZC-DOSE-24H-02 | `dose_24h_window_rolls.json` | ZC | Volume dosed more than 24 h ago no longer counts (rolling window) | §9.8 | TODO (decision D2) |
| ZC-DOSE-24H-03 | `dose_24h_counts_delivered.json` | ZC | Counters add delivered mL (partial/stopped amounts), not requested mL | §9.8, §6.6 | TODO |
| ZC-DOSE-DAYTANK-01 | `dose_refused_daytank_low.json` | ZC | Day-tank holds less than the requested line → `REJECTED` `DAYTANK_LOW` | §9.1, §8.4 | TODO |
| ZC-DOSE-RESERVOIR-01 | `dose_refused_reservoir_low.json` | ZC | Reservoir below `minReservoirPctToDose` → `REJECTED` `RESERVOIR_LOW` | §7.7, §8.4 | TODO |
| ZC-DOSE-STALE-01 | `dose_refused_ec_stale.json` | ZC | EC status `STALE` → `REJECTED` `SENSOR_STALE` | §8.4, §14 | TODO |
| ZC-DOSE-STALE-02 | `dose_refused_ph_fault.json` | ZC | pH status `FAULT` (value null) → `REJECTED` `SENSOR_STALE` | §8.4, §6.1 | TODO |
| ZC-DOSE-STOPPED-01 | `dose_refused_zone_stopped.json` | ZC | After STOP ZONE (no E-STOP) → DOSE `REJECTED` `ZONE_STOPPED` until RESUME | §8.3.8 | TODO |
| ZC-DOSE-LATCHED-01 | `dose_refused_fault_latched.json` | ZC | With latch `CABINET_POWER_CUT` → `REJECTED` `FAULT_LATCHED` | §6.8, §8.4 | TODO |
| ZC-DOSE-INVALID-01 | `dose_refused_malformed.json` | ZC | Missing `lines`, negative or fractional `ml` → `REJECTED` `INVALID_COMMAND` | §8.4, §1 | TODO |
| ZC-DOSE-INVALID-02 | `dose_refused_unknown_line.json` | ZC | Line not in this zone's `doseLines` (for example BASE) → `REJECTED` `INVALID_COMMAND` | §2.3, OPEN-4, OPEN-12 | TODO |
| ZC-CHECK-ORDER-01 | `dose_check_precedence.json` | ZC | Several checks fail at once → the agreed first reason is reported | §9.1 | TODO (decision D1) |

## 2. Command lifecycle: duplicates, expiry, journal, reboot (§8.3, §9 rule 2)

| ID | File | Device | Rule, in one line | Contract | Status |
|---|---|---|---|---|---|
| ZC-CMD-DUP-01 | `cmd_duplicate_after_complete.json` | ZC | Same `cmdId` after COMPLETED → current state re-sent, nothing runs again | §8.3.1 | TODO |
| ZC-CMD-DUP-02 | `cmd_duplicate_while_running.json` | ZC | Same `cmdId` while EXECUTING → current state re-sent, no second run | §8.3.1 | TODO |
| ZC-CMD-DUP-03 | `cmd_duplicate_after_reject.json` | ZC | Resend of a REJECTED `cmdId` after the cause clears → still REJECTED, never executed | §8.3.1 | TODO |
| ZC-CMD-DUP-04 | `cmd_id_memory_is_50.json` | ZC | The 51st distinct `cmdId` evicts the oldest; memory stays bounded at 50 | §8.3.1 | TODO |
| ZC-CMD-EXPIRED-01 | `cmd_expired_on_arrival.json` | ZC | `expiresAt` already passed → `REJECTED` `EXPIRED_ON_ARRIVAL` | §8.3.2, §14 | TODO |
| ZC-CMD-EXPIRED-02 | `cmd_expiry_boundary.json` | ZC | Arrival exactly at `expiresAt` → the agreed answer | §8.3.2 | TODO (decision D4) |
| ZC-JOURNAL-01 | `dose_journal_before_pump.json` | ZC | The journal `nvs_write` happens before the first pump output switches on | §8.3.5, §9.2 | TODO |
| ZC-REBOOT-01 | `reboot_mid_dose_partial.json` | ZC | Reboot during a line → after restart `PARTIAL` `REBOOT_MID_DOSE` with measured amounts | §8.3.6, §9.2 | TODO |
| ZC-REBOOT-02 | `reboot_before_delivery_failed.json` | ZC | Reboot after ACCEPTED, before any delivery → `FAILED` `REBOOT_MID_DOSE` | §8.3.6 | TODO |
| ZC-REBOOT-03 | `reboot_never_resumes.json` | ZC | After restart: outputs off, the journalled dose is never resumed, zone `stopped` until RESUME | §8.3.6, §9.9 | TODO |
| ZC-REBOOT-04 | `reboot_duplicate_not_replayed.json` | ZC | The interrupted `cmdId` resent after reboot is not executed | §8.3.1, §8.3.6 | TODO (decision D3) |
| ZC-REBOOT-05 | `reboot_bootid_and_event.json` | ZC | `bootId` +1 after restart; `REBOOTED` event with `reason` | §5, §6.5 | TODO |

## 3. Dose execution (§7.2, §6.6)

| ID | File | Device | Rule, in one line | Contract | Status |
|---|---|---|---|---|---|
| ZC-DOSE-OK-01 | `dose_completes.json` | ZC | Happy path: ACCEPTED → EXECUTING → COMPLETED, with requested/delivered and `before`/`after` after `mixWaitS` | §6.6, §7.2 | TODO |
| ZC-DOSE-ORDER-01 | `dose_runs_in_order.json` | ZC | Lines run one at a time in `order`, not in list order | §7.2 | TODO |
| ZC-DOSE-PROGRESS-01 | `dose_progress_every_5s.json` | ZC | EXECUTING sent at each line start and end and at least every 5 s while a line runs | §6.6, §14 | TODO |
| ZC-DOSE-NOFLOW-01 | `dose_no_flow.json` | ZC | Pump on, no flow pulses → line stops with `NO_FLOW`, later lines `SKIPPED` | §8.4, §6.6 | TODO |
| ZC-DOSE-SHORT-01 | `dose_flow_short.json` | ZC | Delivered below `(100 − tolerancePct)%` → line `SHORT`, dose `PARTIAL` `FLOW_SHORT` | §7.2, §8.4 | TODO |
| ZC-DOSE-TIMEOUT-01 | `dose_timeout.json` | ZC | `maxDurationS` reached → pumps off, `TIMEOUT` with amounts so far | §8.4, §14 | TODO |
| ZC-DOSE-ESTOP-DURING-01 | `dose_estop_during.json` | ZC | E-STOP mid-line → pumps off at once, `STOPPED` `ESTOP_DURING` with amounts | §8.4, §8.1 | TODO |
| ZC-DOSE-MODE-DURING-01 | `dose_mode_changed_during.json` | ZC | Key switch leaves REMOTE mid-dose → `STOPPED` `MODE_CHANGED_DURING` | §8.4 | TODO |
| ZC-DOSE-STOP-01 | `dose_stop_requested.json` | ZC | STOP ZONE mid-dose → `STOPPED` `STOP_REQUESTED` with amounts; zone `stopped` | §7.4, §8.4 | TODO |
| ZC-DOSE-ZERO-LINE-01 | `dose_zero_ml_line.json` | ZC | A line requested at 0 mL is reported `SKIPPED` and its pump never runs | §6.6 | TODO (decision D5) |

## 4. Refill (§7.3) and auto-refill off in Phase A (§0)

| ID | File | Device | Rule, in one line | Contract | Status |
|---|---|---|---|---|---|
| ZC-REFILL-OK-01 | `refill_to_target.json` | ZC | START → valve opens, closes at `targetPct`; COMPLETED with `detail.fromPct`/`toPct` | §7.3, §6.6 | TODO |
| ZC-REFILL-TIMEOUT-01 | `refill_timeout.json` | ZC | `maxDurationS` reached → valve closes, `TIMEOUT` | §7.3, §8.4 | TODO |
| ZC-REFILL-PAUSE-01 | `refill_pause_resume_stop.json` | ZC | PAUSE closes, RESUME reopens, STOP ends the refill | §7.3 | TODO |
| ZC-REFILL-CHECKS-01 | `refill_safety_checks.json` | ZC | REFILL START refused for ESTOP, NOT_REMOTE, ZONE_STOPPED, FAULT_LATCHED | §9.1, §8.4 | TODO |
| ZC-AUTOREFILL-OFF-01 | `autorefill_off_phase_a.json` | ZC | With `autoRefill.enabled` false, a low reservoir never opens the valve by itself | §0, §7.7 | TODO |

## 5. Stop, resume, latches: nothing restarts by itself (§7.4, §7.8, §7.9, §9 rule 9)

| ID | File | Device | Rule, in one line | Contract | Status |
|---|---|---|---|---|---|
| ZC-STOP-ALL-OPS-01 | `stop_all_operations.json` | ZC | `ALL_OPERATIONS` stops dosing and refill; circulation keeps running | §7.4 | TODO |
| ZC-STOP-ZONE-01 | `stop_other_zone_ignored.json` | ZC | `ZONE` stop for Z2 does not affect ZC1 | §7.4 | TODO |
| ZC-STOP-ALWAYS-01 | `stop_always_accepted.json` | ZC | STOP is acted on in LOCAL, MAINT, E-STOP and stopped states | §7.4 | TODO (OPEN-10 for late stops) |
| ZC-RESUME-ESTOP-01 | `resume_refused_estop.json` | ZC | RESUME while E-STOP pressed → `REJECTED` `ESTOP` | §7.8 | TODO |
| ZC-RESUME-LATCH-01 | `resume_refused_latched.json` | ZC | RESUME while a latch is active → `REJECTED` `FAULT_LATCHED` | §7.8 | TODO |
| ZC-RESUME-OK-01 | `resume_restarts_nothing.json` | ZC | RESUME clears `stopped`; the interrupted dose is not restarted | §7.8, §9.9 | TODO |
| ZC-ESTOP-RELEASE-01 | `estop_release_stays_stopped.json` | ZC | Releasing the E-STOP leaves the zone `stopped` until RESUME | §6.8, §9.9 | TODO |
| ZC-CLEARFAULT-01 | `clear_fault_then_resume.json` | ZC | CLEAR_FAULT removes the latch; the zone still needs RESUME | §7.9 | TODO |
| ZC-POWERCUT-01 | `power_cut_outputs_off.json` | ZC | Power cut → all outputs off after restart, zone `stopped` | §9.9, 11.4.3 | TODO |
| ZC-STUCK-RELAY-01 | `stuck_relay_latches.json` | ZC | Pump stuck on → `CABINET_POWER_CUT` latch, zone stopped | §6.8, 11.4.3 | TODO |

## 6. Circulation is protected (§9 rule 4, §2.3)

| ID | File | Device | Rule, in one line | Contract | Status |
|---|---|---|---|---|---|
| ZC-CIRC-01 | `circulation_command_refused.json` | ZC | Any command that would switch circulation → `REJECTED` `CIRC_PROTECTED`; CIRC stays on | §9.4, §8.4 | TODO |
| ZC-CIRC-02 | `circulation_survives_stop_all.json` | ZC | STOP `ALL_EQUIPMENT` never switches circulation off | §7.4, §9.4 | TODO |

## 7. Config kept through a connection loss (§7.7, §9 rule 3)

| ID | File | Device | Rule, in one line | Contract | Status |
|---|---|---|---|---|---|
| ZC-CONFIG-01 | `config_applied_and_reported.json` | ZC | New DEVICE_CONFIG applied; `configVersion` reported in ZONE_STATE and DEVICE_STATUS | §7.7, §6.8 | TODO |
| ZC-CONFIG-02 | `config_kept_offline.json` | ZC | During a link drop the last good config keeps being enforced | §9.3 | TODO |
| ZC-CONFIG-03 | `config_invalid_ignored.json` | ZC | A malformed DEVICE_CONFIG is ignored and the last good one kept | §7.7 | TODO (decision D6) |
| ZC-CONFIG-04 | `config_survives_reboot.json` | ZC | The last good config is still in force after a reboot | §9.3 | TODO (decision D6) |

## 8. Reporting every change (§9 rules 6 and 10, §6.5, §6.6, §6.8)

| ID | File | Device | Rule, in one line | Contract | Status |
|---|---|---|---|---|---|
| ZC-REPORT-CMD-01 | `report_every_cmd_state.json` | ZC | Every command state change is reported with CMD_STATE | §9.6, §6.6 | TODO |
| ZC-REPORT-ZONE-01 | `report_zone_state_outputs.json` | ZC | Every output change → ZONE_STATE | §9.6, §6.8 | TODO |
| ZC-REPORT-ZONE-02 | `report_zone_state_mode_stop_latch.json` | ZC | Mode, stopped and latch changes → ZONE_STATE | §9.6, §6.8 | TODO |
| ZC-REPORT-ALARM-01 | `telemetry_at_once_on_alarm.json` | ZC | ZONE_TELEMETRY sent at once when a value crosses an alarm threshold | §6.1, §7.7 | TODO |
| ZC-EVENT-01 | `events_have_three_answers.json` | ZC | Every EVENT has `message`, `systemAction`, `nextStep` | §9.10, §6.5 | TODO |
| ZC-EVENT-MODE-01 | `event_mode_changed.json` | ZC | MODE_CHANGED with `from`/`to`; WARNING when leaving REMOTE | §6.5 | TODO |
| ZC-EVENT-FAULTS-01 | `events_leak_flow_sensor_levels.json` | ZC | LEAK, CIRC_NO_FLOW, SENSOR_FAULT, RESERVOIR_LOW, DAYTANK_LOW, PUMP_FAULT events and their clears | §6.5 | TODO |

## 9. Climate controller CC1 (§7.5, §7.6, §9 rule 5)

| ID | File | Device | Rule, in one line | Contract | Status |
|---|---|---|---|---|---|
| CC1-AUTO-OFF-01 | `climate_automatic_refused.json` | CC1 | CLIMATE_MODE AUTOMATIC → `REJECTED` `FEATURE_OFF` (Phase A) | §7.5, §9.5 | TODO |
| CC1-MANUAL-01 | `climate_manual_no_limit.json` | CC1 | In Phase A climate stays MANUAL with no time limit (`manualUntil` null) | §7.5 | TODO |
| CC1-NOT-MANUAL-01 | `climate_equipment_not_manual.json` | CC1 | Equipment command while AUTOMATIC → `NOT_MANUAL` (only when automation is switched on) | §7.6 | TODO (later phase) |
| CC1-PAD-01 | `pad_needs_fans.json` | CC1 | PAD1 ON at fan stage 0 → `REJECTED` `NEEDS_FANS` | §7.6, §8.4 | TODO |
| CC1-FANS-01 | `fans_zero_stops_pad.json` | CC1 | SET_STAGE 0 also switches PAD1 off | §7.6 | TODO |
| CC1-FANS-02 | `fans_stage_above_layout.json` | CC1 | Stage above the number of fans in the layout → `INVALID_COMMAND` | §7.6, OPEN-12 | TODO |
| CC1-CHILLER-01 | `chiller_setpoint_range.json` | CC1 | Setpoint outside the `climate.json` range (18–28 °C) → `INVALID_COMMAND` | §7.6 | TODO |
| CC1-FOG-01 | `fogger_not_installed.json` | CC1 | FOG1 RUN → `REJECTED` `NOT_INSTALLED` | §7.6, §2.3 | TODO |
| CC1-STOP-01 | `climate_stop_all_equipment.json` | CC1 | STOP `ALL_EQUIPMENT` → fans, pad, chillers off; stopped until RESUME (scope CLIMATE) | §7.4, §7.8 | TODO |
| CC1-REPORT-01 | `climate_state_on_change.json` | CC1 | Every equipment, mode, stop or latch change → CLIMATE_STATE | §9.6, §6.9 | TODO |

## Not controller vectors (listed so nothing is lost)

| Rule | Owner | Contract |
|---|---|---|
| `ROLE_FORBIDDEN`, `DEVICE_OFFLINE`, `EC_CEILING`, `PH_BOUNDS`, `NEEDS_APPROVAL_DECLINED`, `STATE_UNKNOWN` | Cloud (Track 1) | §8.4 (cloud-only codes) |
| Cloud marks `EXPIRED` when no ACCEPTED arrives in time; never resends (§8.3.2–3) | Cloud (Track 1) | §8.3 |
| `UNKNOWN` and its reconciliation | Cloud (Track 1) | §8.1, §8.2 |
| `ts` real time and `simTs` simulated time on every message (§9 rule 7) | Message builder (Track 4) | §5, §11.4 |
| Gateway: hold, journal, replay in order (§10) | GW1 (Track 4) | §6.7, §10 |
| PM1 has no commands; POWER_STATE content | Device layer (Track 4) | §6.3 |

## Decisions these vectors need (to settle in `docs/controller-spec.md` or the contract session)

| # | Question |
|---|---|
| D1 | Order of the own safety checks when several fail at once. `ZC-DOSE-ESTOP-01` assumes **ESTOP is checked first** (so it wins over ZONE_STOPPED). |
| D2 | Do the 24 h totals follow real time or the simulator's clock? On playground sites the clock can run 288× (§11.4). |
| D3 | Must the last-50 `cmdId` memory survive a reboot (in `nvs`)? If not, a duplicate arriving after a reboot could execute. The safe answer is yes. |
| D4 | Is a command that arrives exactly at `expiresAt` expired? |
| D5 | Is a 0 mL line `SKIPPED` or `DONE`? |
| D6 | What does a controller do with a malformed DEVICE_CONFIG, and is the last good config kept in `nvs` across reboots? |
