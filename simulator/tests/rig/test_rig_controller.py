"""sim/controller's zone controller on the prototype rig's HAL (T1-32): timed doses.

A line's mL become one sf `dose` of mL / pump rate seconds; the line ends when the rig reports the
pump stopped, with what it really ran (elapsed_s x rate). A stop waits for that report, so the
mL a STOPPED dose gives are measured. client_A's pumps run 2 mL/s (hardware.json).
"""

from __future__ import annotations

import re

from rig_support import Rig, make_rig_pack

from sim.rig.sf import dose_id_for


def lines_of(state):
    return [(line["line"], line["deliveredMl"], line["state"]) for line in state["lines"]]


def test_a_dose_sends_one_timed_sf_dose_per_line_and_finishes_with_the_measured_ml(rig):
    cmd_id = rig.dose(A=120, B=60)
    [first] = rig.doses_sent()
    assert first["target"] == "zone:1" and first["cmd"] == "dose"
    assert first["args"] == {"relay": "relay1", "seconds": 60.0,
                             "dose_id": dose_id_for(f"{cmd_id}/A")}
    assert first["id"] == first["args"]["dose_id"]
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,32}", first["args"]["dose_id"])  # sf/cmd/1's pattern
    assert abs(first["issued_at"] - rig.clock.unix) <= 1  # the rig refuses one 60 s old

    rig.rig_reports(first, "running")
    rig.step(30)
    assert rig.states(cmd_id)[-1]["state"] == "EXECUTING"
    rig.rig_reports(first, "done", elapsed=60.0)
    rig.step()
    second = rig.doses_sent()[1]  # one line at a time, in `order`
    assert second["args"]["relay"] == "relay2" and second["args"]["seconds"] == 30.0
    rig.rig_reports(second, "running")
    rig.rig_reports(second, "done", elapsed=30.0)
    rig.step()
    rig.step()  # mixWaitS 0: `after` on the next step
    final = rig.states(cmd_id)[-1]
    assert final["state"] == "COMPLETED" and final["after"] is not None
    assert lines_of(final) == [("A", 120, "DONE"), ("B", 60, "DONE")]


def test_a_dose_counts_what_the_rig_says_ran_not_what_was_asked(rig):
    cmd_id = rig.dose(A=100)
    [sent] = rig.doses_sent()
    rig.rig_reports(sent, "done", elapsed=40.0)  # a pump that ran short: 80 mL at 2 mL/s
    rig.step()
    final = rig.states(cmd_id)[-1]
    assert (final["state"], final["reason"]) == ("PARTIAL", "FLOW_SHORT")
    assert lines_of(final) == [("A", 80, "SHORT")]


def test_stop_switches_the_relay_off_and_the_stopped_state_waits_for_the_rigs_measurement(rig):
    cmd_id = rig.dose(A=300)
    [sent] = rig.doses_sent()
    rig.rig_reports(sent, "running")
    rig.step(20)
    stop = rig.command("STOP", target={"scope": "ZONE", "zone": "Z1"})
    off = rig.sent[-1]
    assert off["cmd"] == "switch" and off["args"] == {"relays": {"relay1": False}}
    assert [s["state"] for s in rig.states(stop)] == ["ACCEPTED", "COMPLETED"]
    assert rig.states(cmd_id)[-1]["state"] == "EXECUTING"  # not STOPPED with a guess
    rig.rig_reports(sent, "stopped", elapsed=21.5, reason="command")
    rig.step()
    final = rig.states(cmd_id)[-1]
    assert (final["state"], final["reason"]) == ("STOPPED", "STOP_REQUESTED")
    assert lines_of(final) == [("A", 43, "SHORT")]
    assert rig.controller.stopped
    assert rig.states(rig.dose(A=10))[-1]["reason"] == "ZONE_STOPPED"
    assert [s["state"] for s in rig.states(rig.command("RESUME"))] == ["ACCEPTED", "COMPLETED"]


def test_a_stop_report_that_never_comes_closes_after_30_s_and_counts_later(rig):
    cmd_id = rig.dose(A=300)
    [sent] = rig.doses_sent()
    rig.rig_reports(sent, "running")
    rig.command("STOP", target={"scope": "ZONE", "zone": "Z1"})
    rig.step(31)
    final = rig.states(cmd_id)[-1]
    assert (final["state"], final["reason"]) == ("STOPPED", "STOP_REQUESTED")
    assert lines_of(final) == [("A", 0, "SHORT")]  # nothing measured in time
    rig.rig_reports(sent, "stopped", elapsed=10.0, reason="command")  # late, but it ran
    rig.step()
    items = rig.hal.nvs_read("doseHistory")["items"]
    assert [(line, ml) for _, line, ml in items] == [("A", 20)]  # still in the 24 h totals


def test_a_dose_the_rig_refuses_ends_failed_and_runs_nothing(rig):
    cmd_id = rig.dose(A=50)
    [sent] = rig.doses_sent()
    rig.hal.on_cmd_result({"schema": "sf/cmd-result/1", "gateway": "gateway-01", "boot": 7,
                           "seq": 3, "id": sent["id"], "target": "zone:1", "cmd": "dose",
                           "status": "refused", "result": "refused-interlock", "value": None,
                           "detail": None})
    rig.step()
    final = rig.states(cmd_id)[-1]
    assert final["state"] == "FAILED" and lines_of(final) == [("A", 0, "SHORT")]


def test_an_estop_on_the_rig_stops_the_dose_with_what_it_ran(rig):
    cmd_id = rig.dose(A=300)
    [sent] = rig.doses_sent()
    rig.rig_reports(sent, "running")
    rig.clock.advance(10)
    rig.hal.update({**rig.hal.latest, "seq": 99, "ts": int(rig.clock.unix),
                    "actuators": {**rig.hal.latest["actuators"], "estop": True}})
    rig.controller.step(rig.hal.now_ms())
    events = [b["kind"] for k, b in rig.reported if k == "EVENT"]
    assert events == ["ESTOP_PRESSED"]
    rig.rig_reports(sent, "stopped", elapsed=10.0, reason="estop")
    rig.controller.step(rig.hal.now_ms())
    final = rig.states(cmd_id)[-1]
    assert (final["state"], final["reason"]) == ("STOPPED", "ESTOP_DURING")
    assert lines_of(final) == [("A", 20, "SHORT")]


def test_a_lost_dose_command_times_out_at_max_duration(rig):
    cmd_id = rig.dose(A=50)
    rig.step(601)  # no word from the rig at all
    final = rig.states(cmd_id)[-1]
    assert (final["state"], final["reason"]) == ("FAILED", "TIMEOUT")


def test_the_rigs_drums_have_no_level_sensors_so_the_day_tank_check_is_left_to_a_person(rig):
    readings, quality = rig.hal.readings()
    assert readings["dayTankPct"] == {"A": None, "B": None, "ACID": None}
    assert "dayTankPct" not in quality  # not fitted: no quality, as doMgL
    assert rig.states(rig.dose(A=10))[0]["state"] == "ACCEPTED"


def test_refill_is_refused_because_the_rig_fills_itself(rig):
    cmd_id = rig.command("REFILL", action="START", targetPct=90, maxDurationS=600)
    assert [(s["state"], s["reason"]) for s in rig.states(cmd_id)] == [("REJECTED", "FEATURE_OFF")]


def test_doses_are_refused_on_stale_or_missing_readings(rig):
    rig.hal.update({**rig.hal.latest, "seq": 50, "ts": int(rig.clock.unix),
                    "water_quality": {**rig.hal.latest["water_quality"], "ph": None}})
    assert rig.states(rig.dose(A=10))[0]["reason"] == "SENSOR_STALE"
    rig.hal.update({**rig.hal.latest, "seq": 51, "ts": int(rig.clock.unix) - 300,
                    "water_quality": {**rig.hal.latest["water_quality"], "ph": 6.1}})
    assert rig.states(rig.dose(A=10))[0]["reason"] == "SENSOR_STALE"  # 5 minutes old


def test_a_gateway_restart_mid_dose_on_zone_1_stops_the_box_and_waits_for_what_it_ran(rig):
    """Zone 1's actuator box finishes a dose on time whatever the link does: restart() switches
    it off, and REBOOT_MID_DOSE waits for the box's report, so its mL are measured."""
    cmd_id = rig.dose(A=300)
    [sent] = rig.doses_sent()
    rig.rig_reports(sent, "running")
    rig.step(5)
    rig.hal.gateway_restarted()
    rig.controller.restart("REBOOT")
    off = rig.sent[-1]
    assert off["cmd"] == "switch" and off["args"] == {"relays": {"relay1": False}}
    assert rig.states(cmd_id)[-1]["state"] == "EXECUTING"  # not closed with a guess
    assert rig.controller.stopped
    assert [b["kind"] for k, b in rig.reported if k == "EVENT"] == ["REBOOTED"]
    rig.command("RESUME")
    assert rig.states(rig.dose(B=10))[-1]["reason"] == "BUSY"  # the box's dose isn't over
    rig.rig_reports(sent, "stopped", elapsed=6.0, reason="command")
    rig.command("STOP", target={"scope": "ZONE", "zone": "Z1"})  # before the next step
    final = rig.states(cmd_id)[-1]
    assert (final["state"], final["reason"]) == ("PARTIAL", "REBOOT_MID_DOSE")  # first reason
    assert lines_of(final) == [("A", 12, "SHORT")]
    rig.step()
    items = rig.hal.nvs_read("doseHistory")["items"]
    assert [(line, ml) for _, line, ml in items] == [("A", 12)]  # counted once


def test_a_box_silent_after_a_gateway_restart_closes_after_30_s_and_counts_later(rig):
    cmd_id = rig.dose(A=300)
    [sent] = rig.doses_sent()
    rig.rig_reports(sent, "running")
    rig.hal.gateway_restarted()
    rig.controller.restart("REBOOT")
    rig.step(31)
    final = rig.states(cmd_id)[-1]
    assert (final["state"], final["reason"]) == ("FAILED", "REBOOT_MID_DOSE")
    assert lines_of(final) == [("A", 0, "SHORT")]  # nothing measured in time
    rig.rig_reports(sent, "stopped", elapsed=10.0, reason="command")  # late, but it ran
    rig.step()
    items = rig.hal.nvs_read("doseHistory")["items"]
    assert [(line, ml) for _, line, ml in items] == [("A", 20)]  # still in the 24 h totals


def test_a_gateway_restart_mid_dose_on_zone_2_closes_it_at_once(tmp_path, clock):
    """Zone 2's pumps are the gateway's own relays: they came up OFF with it and the dose they
    ran won't report back, so the command closes at once and nothing is sent to the rig."""
    rig = Rig(tmp_path, make_rig_pack(tmp_path, rigZone=2), clock)
    cmd_id = rig.dose(A=300)
    [sent] = rig.doses_sent()
    assert sent["target"] == "zone:2"
    rig.rig_reports(sent, "running")
    rig.step(5)
    rig.hal.gateway_restarted()
    rig.controller.restart("REBOOT")
    assert rig.sent == [sent]  # no switch: those relays are off already
    final = rig.states(cmd_id)[-1]
    assert (final["state"], final["reason"]) == ("FAILED", "REBOOT_MID_DOSE")
    assert rig.controller.stopped
    assert [b["kind"] for k, b in rig.reported if k == "EVENT"] == ["REBOOTED"]


def test_what_a_dose_delivered_goes_into_the_24h_totals_once(rig):
    rig.dose(A=100)
    [sent] = rig.doses_sent()
    rig.rig_reports(sent, "done", elapsed=50.0)
    rig.rig_reports(sent, "done", elapsed=50.0)  # QoS 1 again; the link drops repeats too
    rig.step()
    rig.step()
    items = rig.hal.nvs_read("doseHistory")["items"]
    assert [(line, ml) for _, line, ml in items] == [("A", 100)]
