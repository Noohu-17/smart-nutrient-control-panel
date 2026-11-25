"""A zone controller (ZC1, ZC2 ...): its rules, journal and reports (contract 8.3, 9).

It touches the farm only through the HAL, and speaks only through report(type, body): the device
layer adds the envelope and the topic. handle() takes a command; step() runs at a fixed rate and
never waits.

A dose runs its lines one at a time, each until its flow meter has counted the request, then waits
mixWaitS on the controller's clock (simulated time in the simulator) before reading `after`. A
refill runs until the reservoir reaches its target. Both stop at maxDurationS.

On a HAL whose pumps run by time (hal.TimedPumps, the prototype rig), each line asks for its mL at
once and ends when the hardware reports the pump has stopped. A stop waits for that report, so
the mL it gives are measured, and mL reported after a line ended still count in the 24 h totals.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..messages import utc_ts
from . import checks
from .hal import HAL

log = logging.getLogger("sim.controller")

RECENT_CMD_MAX = 50  # contract 8.3 rule 1
CIRC_PUMP, REFILL_VALVE = "CIRC_PUMP", "REFILL_VALVE"
INPUT_ESTOP, INPUT_REMOTE, INPUT_MAINT = "ESTOP", "MODE_REMOTE", "MODE_MAINT"
NVS_JOURNAL, NVS_RECENT = "journal", "recentCmds"
NVS_DOSED, NVS_LATCHES = "doseHistory", "latches"
PROGRESS_MS = 5000  # contract 6.6, 14: EXECUTING at least every 5 s while a line runs
JOURNAL_MS = 5000  # the measured mL go to nvs this often on the controller's own clock
STOP_WAIT_MS = 30_000  # a timed pump's report of what it ran, after a stop (the rig's own timeout)
# Contract 14's defaults, for when neither the DOSE nor limits.json "dose" gives one.
DOSE_DEFAULTS = {"maxDurationS": 600, "mixWaitS": 300, "tolerancePct": 5}
RUNNING = ("ACCEPTED", "EXECUTING")

Report = Callable[[str, dict[str, Any]], None]


@dataclass
class Operation:
    """The dose or refill running now, or a dose in its mix wait (contract 6.8)."""

    cmd_id: str
    kind: str
    state: str = "ACCEPTED"
    reason: str | None = None
    line: str | None = None
    lines: list[dict[str, Any]] = field(default_factory=list)
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    started_at: str | None = None
    ended_at: str | None = None
    detail: dict[str, Any] | None = None
    measured_ml: dict[str, float] = field(default_factory=dict)  # what the flow meters counted
    tolerance_pct: float = 0.0
    max_run_ms: int = 0
    mix_wait_ms: int = 0
    target_pct: float | None = None
    deadline_ms: int | None = None  # on the controller's clock, like the mix wait
    mix_until_ms: int | None = None
    progress_utc_ms: int = 0  # real time: progress is for the people watching
    journal_ms: int = 0  # the controller's clock: a reboot loses at most JOURNAL_MS of pumping
    # (state, reason, until ms): a timed pump stopping. State None: PARTIAL or FAILED by what ran.
    ending: tuple[str | None, str, int] | None = None


class ZoneController:
    """One zone's rules. start() begins a run of the simulator; restart() follows a reboot."""

    def __init__(
        self,
        hal: HAL,
        zone_id: str,
        dose_lines: Iterable[str],
        config: Mapping[str, Any],
        day_tank_usable_l: Mapping[str, float],
        report: Report,
        *,
        device_id: str = "",
        zone_name: str | None = None,
        stopped: bool = False,
        latches: Iterable[str] = (),
        dose_history: Iterable[Iterable[Any]] = (),
        recent: Iterable[Mapping[str, Any]] = (),
        unmonitored_tanks: Iterable[str] = (),
        refill_available: bool = True,
    ):
        """unmonitored_tanks: lines whose day-tank has no level sensor; refill_available: False
        for a zone that fills itself, so REFILL is refused FEATURE_OFF (both checks.Facts)."""
        self.hal, self.zone_id, self._report = hal, zone_id, report
        self.dose_lines = tuple(dose_lines)
        self.config, self.day_tank_usable_l = config, day_tank_usable_l
        self.unmonitored_tanks = frozenset(unmonitored_tanks)
        self.refill_available = refill_available
        self._timed = bool(getattr(hal, "timed_pumps", False))  # hal.TimedPumps
        self.device_id, self.zone_name = device_id, zone_name or zone_id
        self.stopped = stopped
        self.latches = set(latches)
        self._history = [list(entry) for entry in dose_history]
        self._recent = {
            item["cmdId"]: _cmd_state_body(item["cmdId"], item["state"], item.get("reason"))
            for item in recent
        }
        self._operation: Operation | None = None
        self._outputs = {CIRC_PUMP: True, REFILL_VALVE: False}
        self._outputs.update({f"PUMP_{line}": False for line in self.dose_lines})
        self._estop = hal.read_input(INPUT_ESTOP)
        self._mode = self._read_mode()
        self._last_state = self._zone_state()

    # --- commands ------------------------------------------------------------------------

    def handle(self, cmd: Mapping[str, Any]) -> None:
        """One command, as received. Every rule on the way is in checks.refusal()."""
        cmd_id = cmd.get("cmdId") if isinstance(cmd, Mapping) else None
        if not isinstance(cmd_id, str) or not cmd_id:
            log.warning("%s dropped a command with no readable cmdId", self.zone_id)
            return  # it can't be answered (contract 6.6)
        known = self._recent.get(cmd_id)
        if known is not None:  # one command ID, one action (contract 8.3 rule 1)
            self._report("CMD_STATE", dict(known))
            return
        if not self._stop_applies(cmd):
            return  # another zone's: answered by that zone's controller (contract 8.3 rule 10)
        reason = checks.refusal(cmd, self.zone_id, self.dose_lines, self._facts())
        if reason:
            self._send_cmd_state(cmd_id, "REJECTED", reason, lines=_rejected_lines(cmd))
        elif cmd["kind"] == "DOSE":
            self._start_dose(cmd)
        elif cmd["kind"] == "REFILL":
            self._start_refill(cmd)
        else:
            self._run_instant(cmd)

    def _stop_applies(self, cmd: Mapping[str, Any]) -> bool:
        target = cmd.get("target")
        if cmd.get("kind") != "STOP" or not isinstance(target, Mapping):
            return True
        return target.get("scope") != "ZONE" or target.get("zone") in (self.zone_id, None)

    def _start_dose(self, cmd: Mapping[str, Any]) -> None:
        requested = {item["line"]: item["ml"] for item in cmd["lines"]}
        lines = [
            {
                "line": line,
                "requestedMl": requested[line],
                "deliveredMl": 0,
                "state": "PENDING" if requested[line] > 0 else "SKIPPED",  # contract 9 rule 8
            }
            for line in cmd["order"]
        ]
        settings = {**DOSE_DEFAULTS, **(self.config.get("dose") or {})}
        settings.update({key: cmd[key] for key in DOSE_DEFAULTS if key in cmd})  # 7.2: it may say
        operation = Operation(
            cmd["cmdId"],
            "DOSE",
            lines=lines,
            tolerance_pct=settings["tolerancePct"],
            max_run_ms=round(settings["maxDurationS"] * 1000),
            mix_wait_ms=round(settings["mixWaitS"] * 1000),
        )
        self._accept(operation)
        first = next(line for line in lines if line["state"] == "PENDING")
        self._start_line(operation, first)
        operation.before = self._ec_ph()
        self._run(operation, f"PUMP_{first['line']}")

    def _start_refill(self, cmd: Mapping[str, Any]) -> None:
        operation = Operation(
            cmd["cmdId"],
            "REFILL",
            target_pct=cmd["targetPct"],
            max_run_ms=round(cmd["maxDurationS"] * 1000),
        )
        self._accept(operation)
        operation.detail = {"fromPct": self.hal.read_sensor("RESERVOIR_PCT")[0]}
        self._run(operation, REFILL_VALVE)

    def _accept(self, operation: Operation) -> None:
        """Journal first: nothing is energised until the command is written (8.3 rule 5)."""
        self._operation = operation
        self._write_journal(operation)
        self._send_operation_state(operation)
        self._report_state_if_changed()

    def _run(self, operation: Operation, output: str) -> None:
        operation.state = "EXECUTING"
        operation.started_at = self._utc_now()
        operation.deadline_ms = self.hal.now_ms() + operation.max_run_ms
        operation.progress_utc_ms = self.hal.utc_ms()
        self._write_journal(operation)
        if operation.kind == "DOSE":
            self._pump_on(operation)
        else:
            self._switch(output, True)
        self._send_operation_state(operation)
        self._report_state_if_changed()

    def _run_instant(self, cmd: Mapping[str, Any]) -> None:
        """STOP, RESUME and CLEAR_FAULT have no run phase (contract 6.6)."""
        self._send_cmd_state(cmd["cmdId"], "ACCEPTED")
        if cmd["kind"] == "STOP":
            self._end_operation("STOPPED", "STOP_REQUESTED")
            self.stopped = True
        elif cmd["kind"] == "RESUME":
            self.stopped = False  # nothing restarts: a person starts a new one (contract 7.8)
        else:
            self.latches.discard(cmd["fault"])
            self._save_latches()
        self._report_state_if_changed()
        self._send_cmd_state(cmd["cmdId"], "COMPLETED")

    def _end_operation(self, state: str | None, reason: str) -> None:
        """Dosing and refill off, the measured amounts reported. Circulation keeps running.

        A pump that runs by time reports what it delivered once it has stopped, so the end
        waits for that report (step(), up to STOP_WAIT_MS) and the mL it gives are measured.
        """
        operation = self._operation
        for output in self._outputs:
            if output != CIRC_PUMP:
                self._switch(output, False)
        if operation is None:
            return
        if operation.ending is not None:  # already ending: the first reason to end it stands
            state, reason, _ = operation.ending
            if self._timed_pump_running(operation):
                return
        elif operation.state in RUNNING and self._timed_pump_running(operation):
            operation.ending = (state, reason, self.hal.now_ms() + STOP_WAIT_MS)
            return
        self._close(operation, state, reason)

    def _close(self, operation: Operation, state: str | None, reason: str) -> None:
        self._operation = None
        if operation.state not in RUNNING:
            return  # in its mix wait: the final state stands and `after` stays null (6.6)
        for line in operation.lines:
            if line["state"] == "RUNNING":
                self._end_line(operation, line)
            elif line["state"] == "PENDING":
                line["state"] = "SKIPPED"
        if state is None:  # after a reboot: PARTIAL if anything was delivered (8.3 rule 6)
            delivered = any(line["deliveredMl"] > 0 for line in operation.lines)
            state = "PARTIAL" if delivered else "FAILED"
        if operation.kind == "REFILL":
            operation.detail = {**(operation.detail or {}), "toPct": self._reservoir_pct()}
        operation.state, operation.reason, operation.line = state, reason, None
        operation.ended_at = self._utc_now()
        self.hal.nvs_write(NVS_JOURNAL, {})
        self._send_operation_state(operation)

    # --- running a dose or a refill --------------------------------------------------------

    def _start_line(self, operation: Operation, line: dict[str, Any]) -> None:
        line["state"] = "RUNNING"
        operation.line = line["line"]
        self._count_late(line["line"])  # the meter starts from zero
        operation.measured_ml[line["line"]] = 0.0

    def _pump_on(self, operation: Operation) -> None:
        """The running line's pump on: until its meter has counted the request, or, for a pump
        that runs by time, asked for the line's mL at once (hal.TimedPumps)."""
        line = next(item for item in operation.lines if item["state"] == "RUNNING")
        output = f"PUMP_{line['line']}"
        if not self._timed:
            self._switch(output, True)
            return
        self._outputs[output] = True
        remaining = line["requestedMl"] - operation.measured_ml.get(line["line"], 0.0)
        self.hal.run_pump_for(output, remaining, f"{operation.cmd_id}/{line['line']}")

    def _timed_pump_running(self, operation: Operation) -> bool:
        return self._timed and any(
            line["state"] == "RUNNING" and self.hal.pump_running(f"PUMP_{line['line']}")
            for line in operation.lines
        )

    def _count_late(self, line: str) -> None:
        """mL a timed pump reported after its line ended (a lost or late report) still count in
        the 24 h totals (9 rule 8). A flow meter counts nothing with its pump off."""
        ml = self.hal.take_flow_ml(f"FLOW_{line}")
        if self._timed and round(ml) > 0:
            self.record_delivered(line, round(ml))

    def _run_dose(self, operation: Operation, now_ms: int) -> None:
        line = next(item for item in operation.lines if item["state"] == "RUNNING")
        self._measure(operation, line)
        stopped_by_itself = self._timed and not self.hal.pump_running(f"PUMP_{line['line']}")
        if operation.measured_ml[line["line"]] >= line["requestedMl"] or stopped_by_itself:
            self._end_line(operation, line)
            following = next((item for item in operation.lines if item["state"] == "PENDING"), None)
            if following is None:
                self._finish(operation, now_ms)
                return
            self._start_line(operation, following)  # one at a time, in `order` (7.2)
            self._pump_on(operation)
            self._report_progress(operation)
        elif now_ms >= operation.deadline_ms:
            self._end_line(operation, line)
            self._finish(operation, now_ms, "TIMEOUT")
        elif self.hal.utc_ms() - operation.progress_utc_ms >= PROGRESS_MS:
            self._report_progress(operation)
        elif now_ms - operation.journal_ms >= JOURNAL_MS:
            self._write_journal(operation)  # what was measured, should the controller die now

    def _measure(self, operation: Operation, line: dict[str, Any]) -> None:
        name = line["line"]
        operation.measured_ml[name] = operation.measured_ml.get(name, 0.0) + self.hal.take_flow_ml(
            f"FLOW_{name}"
        )
        line["deliveredMl"] = round(operation.measured_ml[name])  # whole mL (contract 1)

    def _end_line(self, operation: Operation, line: dict[str, Any]) -> None:
        """Pump off, then count what the meter saw: only measured mL go into the 24 h totals."""
        self._switch(f"PUMP_{line['line']}", False)
        self._measure(operation, line)
        wanted = line["requestedMl"] * (1 - operation.tolerance_pct / 100)
        line["state"] = "DONE" if operation.measured_ml[line["line"]] >= wanted else "SHORT"
        operation.line = None
        if line["deliveredMl"] > 0:
            self.record_delivered(line["line"], line["deliveredMl"])

    def _finish(self, operation: Operation, now_ms: int, reason: str | None = None) -> None:
        """The final state goes out at once with after null; the mix wait follows (6.6)."""
        for line in operation.lines:
            if line["state"] == "PENDING":
                line["state"] = "SKIPPED"
        delivered = any(line["deliveredMl"] > 0 for line in operation.lines)
        short = any(line["state"] == "SHORT" for line in operation.lines)
        if reason is None and not short:
            operation.state = "COMPLETED"
        elif delivered:
            operation.state, operation.reason = "PARTIAL", reason or "FLOW_SHORT"
        else:
            operation.state, operation.reason = "FAILED", reason or "NO_FLOW"
        operation.ended_at = self._utc_now()
        self.hal.nvs_write(NVS_JOURNAL, {})  # ended: nothing left to report after a reboot
        if delivered:
            operation.mix_until_ms = now_ms + operation.mix_wait_ms
        else:
            self._operation = None
        self._send_operation_state(operation)
        self._report_state_if_changed()

    def _after_mixing(self, operation: Operation) -> None:
        """One more CMD_STATE with the same final state and `after` filled in (6.6)."""
        operation.after = self._ec_ph()
        self._operation = None
        self._send_operation_state(operation)
        self._report_state_if_changed()

    def _run_refill(self, operation: Operation, now_ms: int) -> None:
        level = self._reservoir_pct()
        reached = level is not None and level >= operation.target_pct
        if not reached and now_ms < operation.deadline_ms:
            return
        self._switch(REFILL_VALVE, False)
        start_pct = (operation.detail or {}).get("fromPct")
        operation.detail = {**(operation.detail or {}), "toPct": level}
        if reached:
            operation.state = "COMPLETED"
        else:
            rose = level is not None and start_pct is not None and level > start_pct
            operation.state, operation.reason = ("PARTIAL" if rose else "FAILED"), "TIMEOUT"
        operation.ended_at = self._utc_now()
        self._operation = None
        self.hal.nvs_write(NVS_JOURNAL, {})
        self._send_operation_state(operation)
        self._report_state_if_changed()

    def _report_progress(self, operation: Operation) -> None:
        self._write_journal(operation)  # so a reboot reports what was measured (8.3 rule 6)
        operation.progress_utc_ms = self.hal.utc_ms()
        self._send_operation_state(operation)
        self._report_state_if_changed()

    def _ec_ph(self) -> dict[str, Any]:
        return {"ec": self.hal.read_sensor("EC")[0], "ph": self.hal.read_sensor("PH")[0]}

    def _reservoir_pct(self) -> float | None:
        value, status = self.hal.read_sensor("RESERVOIR_PCT")
        return value if status == "OK" else None

    # --- the fixed-rate step ---------------------------------------------------------------

    def step(self, now_ms: int) -> None:
        """Notice what changed on the panel. Returns at once, like firmware."""
        self._notice_panel()
        operation = self._operation
        if operation is not None:
            if operation.ending is not None:
                state, reason, until = operation.ending
                if not self._timed_pump_running(operation) or now_ms >= until:
                    self._close(operation, state, reason)
            elif operation.kind == "REFILL":
                self._run_refill(operation, now_ms)
            elif operation.state == "EXECUTING":
                self._run_dose(operation, now_ms)
            elif operation.mix_until_ms is not None and now_ms >= operation.mix_until_ms:
                self._after_mixing(operation)
        if self._timed:
            self._count_late_on_idle_lines()
        self._report_state_if_changed()

    def _count_late_on_idle_lines(self) -> None:
        operation, running = self._operation, set()
        if operation is not None:
            running = {line["line"] for line in operation.lines if line["state"] == "RUNNING"}
        for line in self.dose_lines:
            if line not in running:
                self._count_late(line)

    def _notice_panel(self) -> None:
        """The E-STOP and the key switch, each acted on once when it changes."""
        pressed = self.hal.read_input(INPUT_ESTOP)
        if pressed != self._estop:
            self._estop = pressed
            if pressed:
                self._on_estop_pressed()
            else:
                self._on_estop_released()
        mode = self._read_mode()
        if mode != self._mode:
            self._on_mode_changed(self._mode, mode)
            self._mode = mode

    def _on_estop_pressed(self) -> None:
        self.stopped = True
        self._end_operation("STOPPED", "ESTOP_DURING")
        self._send_event(
            "ESTOP_PRESSED",
            "CRITICAL",
            f"E-STOP pressed on the {self.zone_name} dosing cabinet",
            f"All dosing and refill in {self.zone_name} stopped; circulation keeps running",
            "Check the cabinet, release the E-STOP, then press Resume in the app",
            {"source": f"{self.device_id} panel"},
        )

    def _on_estop_released(self) -> None:
        self._send_event(
            "ESTOP_RELEASED",
            "INFO",
            f"E-STOP released on the {self.zone_name} dosing cabinet",
            f"{self.zone_name} stays stopped; nothing restarts by itself",
            "Review what happened, then press Resume in the app",
            {"source": f"{self.device_id} panel"},
        )

    def _on_mode_changed(self, old: str, new: str) -> None:
        if self._operation is not None and new != "REMOTE":
            self._end_operation("STOPPED", "MODE_CHANGED_DURING")
        leaving_remote = old == "REMOTE"
        self._send_event(
            "MODE_CHANGED",
            "WARNING" if leaving_remote else "INFO",
            f"{self.zone_name} key switch moved from {old} to {new}",
            "Dosing and refill from the app are blocked"
            if new != "REMOTE"
            else "Dosing and refill from the app are allowed again",
            "Turn the key switch back to REMOTE to use the app"
            if new != "REMOTE"
            else "No action needed",
            {"from": old, "to": new},
        )

    def _read_mode(self) -> str:
        if self.hal.read_input(INPUT_MAINT):
            return "MAINT"
        return "REMOTE" if self.hal.read_input(INPUT_REMOTE) else "LOCAL"

    # --- faults, counters and a reboot -----------------------------------------------------

    def latch(self, fault: str) -> None:
        """Lock out until a person sends CLEAR_FAULT (contract 6.8, 9 rule 9)."""
        self.latches.add(fault)
        self._save_latches()
        self._report_state_if_changed()

    def record_delivered(self, line: str, ml: int) -> None:
        """Add to the rolling 24 h totals; only what was really delivered counts (9 rule 8)."""
        now_ms = self.hal.now_ms()
        self._history = [
            entry for entry in self._history if now_ms - entry[0] < checks.DAY_MS
        ] + [[now_ms, line, ml]]
        self.hal.nvs_write(NVS_DOSED, {"items": self._history})

    def start(self) -> None:
        """A new run of the simulator. What nvs kept comes back, and an interrupted command is
        closed, never resumed. The zone isn't stopped: a new run is not a reboot (contract 6.8)."""
        self._load_saved()
        self._close_interrupted_command()
        self._report_state_if_changed()

    def restart(self, reason: str) -> None:
        """After a reboot or power cut: outputs safe, stopped, nothing replayed (8.3 rule 6).

        A timed pump that kept running through it (the rig's actuator box finishes a dose on its
        own) has just been switched off, and the interrupted command waits for its report, as a
        stop does, so REBOOT_MID_DOSE carries the mL it measured."""
        for output in self._outputs:
            self._outputs[output] = output == CIRC_PUMP
            self.hal.set_output(output, self._outputs[output])  # the panel starts it, not a command
        self.stopped = True
        self._operation = None
        self._load_saved()
        if not self._wait_for_interrupted_pump():
            self._close_interrupted_command()
        self._send_event(
            "REBOOTED",
            "WARNING",
            f"The {self.zone_name} controller restarted",
            f"All dosing and refill in {self.zone_name} are off; nothing was resumed",
            "Review what happened, then press Resume in the app",
            {"reason": reason},
        )
        self._report_state_if_changed()

    def reset_to_start(self) -> None:
        """Playground only (POST /reset, contract 11.4), not firmware: as a fresh farm's controller.

        Anything running ends STOPPED (STOP_REQUESTED: a person asked for everything to stop),
        a mix wait ends with `after` null, and the zone is not stopped, with no latches and no
        24 h totals. The cmdId memory stays, so a repeated command still gets its old answer.
        An E-STOP the reset released is reported first, as any release is (ESTOP_RELEASED).
        """
        self._notice_panel()
        self._end_operation("STOPPED", "STOP_REQUESTED")
        self.stopped = False
        self.latches.clear()
        self._save_latches()
        self._history = []
        self.hal.nvs_write(NVS_DOSED, {"items": []})
        self._report_state_if_changed()

    def _wait_for_interrupted_pump(self) -> bool:
        """A journalled dose whose timed pump is still running stays open, never resumed, and
        closes REBOOT_MID_DOSE once the pump has reported (step(), up to STOP_WAIT_MS). It
        holds the zone meanwhile, so a dose after an early RESUME is refused BUSY."""
        journal = self.hal.nvs_read(NVS_JOURNAL) or {}
        lines = journal.get("lines") or []
        running = [line for line in lines if line.get("state") == "RUNNING"]
        if not self._timed or journal.get("kind") != "DOSE" or not any(
            self.hal.pump_running(f"PUMP_{line['line']}") for line in running
        ):
            return False
        operation = Operation(
            journal["cmdId"],
            "DOSE",
            state="EXECUTING",
            lines=lines,
            before=journal.get("before"),
            started_at=journal.get("startedAt"),
            measured_ml={line["line"]: float(line["deliveredMl"]) for line in lines},
            tolerance_pct=journal.get("tolerancePct", DOSE_DEFAULTS["tolerancePct"]),
            ending=(None, "REBOOT_MID_DOSE", self.hal.now_ms() + STOP_WAIT_MS),
        )
        self._operation = operation
        return True

    def _close_interrupted_command(self) -> None:
        """A journalled command is reported with what was delivered, and never resumed."""
        journal = self.hal.nvs_read(NVS_JOURNAL) or {}
        cmd_id = journal.get("cmdId")
        if cmd_id is None:
            return
        lines = journal.get("lines") or []
        delivered = any(line["deliveredMl"] > 0 for line in lines)
        for line in lines:
            if line["state"] == "RUNNING" and line["deliveredMl"] > 0:
                # Ended lines were counted as they ended; this one's measured mL count now.
                self.record_delivered(line["line"], line["deliveredMl"])
            if line["state"] in ("RUNNING", "PENDING"):
                line["state"] = "SHORT" if line["deliveredMl"] > 0 else "SKIPPED"
        self.hal.nvs_write(NVS_JOURNAL, {})
        self._send_cmd_state(
            cmd_id,
            "PARTIAL" if delivered else "FAILED",
            "REBOOT_MID_DOSE",
            lines=lines if journal.get("kind") == "DOSE" else None,
            before=journal.get("before"),
            started_at=journal.get("startedAt"),
            ended_at=self._utc_now(),
        )

    def _load_saved(self) -> None:
        """The cmdId memory, 24 h totals and latches, as nvs kept them."""
        self._recent = {item["cmdId"]: item for item in self._nvs_items(NVS_RECENT)}
        self._history = [list(entry) for entry in self._nvs_items(NVS_DOSED)]
        self.latches = set(self._nvs_items(NVS_LATCHES))

    def _nvs_items(self, key: str) -> list[Any]:
        return (self.hal.nvs_read(key) or {}).get("items", [])

    # --- what the controller knows and says ------------------------------------------------

    def _facts(self) -> checks.Facts:
        hal = self.hal
        return checks.Facts(
            now_utc_ms=hal.utc_ms(),
            estop=hal.read_input(INPUT_ESTOP),
            latches=self.latches,
            stopped=self.stopped,
            mode=self._read_mode(),
            busy=self._operation is not None,
            ec=hal.read_sensor("EC"),
            ph=hal.read_sensor("PH"),
            reservoir_pct=hal.read_sensor("RESERVOIR_PCT"),
            day_tanks_ml={line: self._day_tank_ml(line) for line in self.dose_lines},
            dosed_24h_ml=checks.dosed_in_last_24h(self._history, hal.now_ms()),
            device_limits=self.config.get("limits") or {},
            unmonitored_tanks=self.unmonitored_tanks,
            refill_available=self.refill_available,
        )

    def _day_tank_ml(self, line: str) -> float | None:
        value, status = self.hal.read_sensor(f"DAYTANK_{line}_PCT")
        usable_l = self.day_tank_usable_l.get(line)
        if value is None or status != "OK" or usable_l is None:
            return None
        return usable_l * value / 100.0 * checks.ML_PER_L

    def _switch(self, output: str, on: bool) -> None:
        """Every dosing and refill output goes through here, and circulation can't."""
        if output == CIRC_PUMP:
            raise ValueError("circulation is never switched by a command (contract 9 rule 4)")
        if self._outputs[output] != on:
            self._outputs[output] = on
            self.hal.set_output(output, on)

    def _write_journal(self, operation: Operation) -> None:
        operation.journal_ms = self.hal.now_ms()
        self.hal.nvs_write(
            NVS_JOURNAL,
            {
                "cmdId": operation.cmd_id,
                "kind": operation.kind,
                "state": operation.state,
                "lines": [dict(line) for line in operation.lines],
                "before": operation.before,
                "startedAt": operation.started_at,
                "tolerancePct": operation.tolerance_pct,
            },
        )

    def _send_operation_state(self, operation: Operation) -> None:
        self._send_cmd_state(
            operation.cmd_id,
            operation.state,
            operation.reason,
            lines=[dict(line) for line in operation.lines] if operation.kind == "DOSE" else None,
            before=operation.before,
            after=operation.after,
            started_at=operation.started_at,
            ended_at=operation.ended_at,
            detail=dict(operation.detail) if operation.detail else None,
        )

    def _send_cmd_state(
        self,
        cmd_id: str,
        state: str,
        reason: str | None = None,
        *,
        lines: list[dict[str, Any]] | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        started_at: str | None = None,
        ended_at: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Report a command's state, and remember it for a repeat of its cmdId (8.3 rule 1)."""
        body = _cmd_state_body(
            cmd_id, state, reason, lines, before, after=after, started_at=started_at,
            ended_at=ended_at, detail=detail,
        )
        self._recent.pop(cmd_id, None)
        self._recent[cmd_id] = body
        while len(self._recent) > RECENT_CMD_MAX:
            del self._recent[next(iter(self._recent))]
        self.hal.nvs_write(NVS_RECENT, {"items": list(self._recent.values())})
        self._report("CMD_STATE", dict(body))

    def _send_event(
        self,
        kind: str,
        severity: str,
        message: str,
        system_action: str,
        next_step: str,
        detail: dict[str, Any],
    ) -> None:
        """Every event answers three questions (contract 6.5, 9 rule 10)."""
        self._report(
            "EVENT",
            {
                "zone": self.zone_id,
                "kind": kind,
                "severity": severity,
                "message": message,
                "systemAction": system_action,
                "nextStep": next_step,
                "detail": detail,
            },
        )

    def _zone_state(self) -> dict[str, Any]:
        """ZONE_STATE (contract 6.8) without its envelope."""
        return {
            "zone": self.zone_id,
            "mode": self._read_mode(),
            "estop": self.hal.read_input(INPUT_ESTOP),
            "stopped": self.stopped,
            "outputs": self._outputs_by_equipment_id(),
            "operation": self._operation_summary(),
            "latches": sorted(self.latches),
            "configVersion": self.config.get("configVersion"),
        }

    def _outputs_by_equipment_id(self) -> dict[str, str]:
        zone = self.zone_id
        outputs = {
            f"{zone}-CIRC": "ON" if self._outputs[CIRC_PUMP] else "OFF",
            f"{zone}-REFILL": "OPEN" if self._outputs[REFILL_VALVE] else "CLOSED",
        }
        for line in self.dose_lines:
            outputs[f"{zone}-DOSE-{line}"] = "ON" if self._outputs[f"PUMP_{line}"] else "OFF"
        return outputs

    def _operation_summary(self) -> dict[str, Any] | None:
        operation = self._operation
        if operation is None:
            return None
        return {
            "cmdId": operation.cmd_id,
            "kind": operation.kind,
            "state": operation.state,
            "line": operation.line,
        }

    def _report_state_if_changed(self) -> None:
        """Every change of outputs, mode, stop or latches (contract 9 rule 6)."""
        state = self._zone_state()
        if state != self._last_state:
            self._last_state = state
            self._report("ZONE_STATE", dict(state))

    def _save_latches(self) -> None:
        self.hal.nvs_write(NVS_LATCHES, {"items": sorted(self.latches)})

    def _utc_now(self) -> str:
        return utc_ts(datetime.fromtimestamp(self.hal.utc_ms() / 1000.0, UTC))


def _cmd_state_body(
    cmd_id: str,
    state: str,
    reason: str | None = None,
    lines: list[dict[str, Any]] | None = None,
    before: dict[str, Any] | None = None,
    *,
    after: dict[str, Any] | None = None,
    started_at: str | None = None,
    ended_at: str | None = None,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """CMD_STATE (contract 6.6) without its envelope. lines: doses; detail: everything else."""
    body: dict[str, Any] = {"cmdId": cmd_id, "state": state, "reason": reason}
    if lines is not None:
        body["lines"] = lines
    body.update(before=before, after=after, startedAt=started_at, endedAt=ended_at)
    if detail is not None:
        body["detail"] = detail
    return body


def _rejected_lines(cmd: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    """Nothing ran: every readable line is SKIPPED. Not a dose, or not readable: no lines."""
    lines = cmd.get("lines")
    if cmd.get("kind") != "DOSE" or not isinstance(lines, list):
        return None
    return [
        {"line": item["line"], "requestedMl": item["ml"], "deliveredMl": 0, "state": "SKIPPED"}
        for item in lines
        if isinstance(item, Mapping) and isinstance(item.get("line"), str)
        and isinstance(item.get("ml"), int)
    ]
