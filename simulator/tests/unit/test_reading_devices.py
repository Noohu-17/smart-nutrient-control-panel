"""Zone, climate and power devices: topics, QoS, retain, envelope, order and failures (T4-5).

Everything goes through SimDevice.session() with a fake MQTT client, the same path as on a
real broker (tests/integration/test_telemetry_broker.py runs it for real).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

import aiomqtt
import pytest
from app.contract import parse_farm_message

from sim.alarms import climate_alarms, zone_alarms
from sim.clock import SimClock, TickLoop
from sim.config import load_devices, load_site, read_pack_file
from sim.devices.base import OUTBOX_MAX, SimDevice
from sim.devices.climate import ClimateDevice
from sim.devices.power import PowerDevice
from sim.devices.readings import ReadingDevice
from sim.devices.zone import ZoneDevice
from sim.farm import load_farm
from sim.main import make_device
from sim.model.water import ReservoirState
from sim.settings import load_settings

CLIENT_A = load_settings({}).client_dir
REAL = datetime(2026, 10, 29, 9, 0, tzinfo=UTC)
SIM_START = datetime(2026, 7, 1, 6, 0, tzinfo=UTC)
SPEEDS = (0, 1, 60, 288)
ENVELOPE_KEYS = {"v", "type", "msgId", "ts", "site", "device", "bootId", "seq"}  # contract 5


class FakeClient:
    """Records publishes. fail_on: raise MqttError from that publish (1 is the first)."""

    def __init__(self, fail_on=None):
        self.published = []
        self.fail_on = fail_on
        self.attempts = 0

    async def publish(self, topic, payload, qos, retain, timeout=None):
        self.attempts += 1
        if self.attempts == self.fail_on:
            raise aiomqtt.MqttError("Disconnected during publish")
        self.published.append({"topic": topic, "qos": qos, "retain": retain,
                               "message": json.loads(payload)})

    @property
    def messages(self):
        return self._forever()

    async def _forever(self):
        await asyncio.Event().wait()
        yield


@pytest.fixture
def farm():
    return load_farm(CLIENT_A)


def zone_device(farm, *, clock=None, boot_id=7, heartbeat_s=3600.0):
    alarms = zone_alarms(read_pack_file(CLIENT_A, "alerts.json"), "Z1", ["A", "B", "ACID"])
    return ZoneDevice("ZC1", "ZONE_CONTROLLER", "siet-ph1", boot_id, zone=farm.zones["Z1"],
                      alarms=alarms, clock=clock, heartbeat_s=heartbeat_s, now=lambda: REAL)


def climate_device(farm, *, clock=None):
    return ClimateDevice("CC1", "CLIMATE_CONTROLLER", "siet-ph1", 3, climate=farm.climate,
                         alarms=climate_alarms(farm.climate.setpoints), clock=clock,
                         heartbeat_s=3600.0, now=lambda: REAL)


def power_device(farm, *, clock=None):
    return PowerDevice("PM1", "POWER_MONITOR", "siet-ph1", 2, power=farm.power, clock=clock,
                       heartbeat_s=3600.0, now=lambda: REAL)


async def run_session(device, client, *, act=lambda: None, settle_s=0.02):
    """Run one session long enough for the outbox to drain, then stop it cleanly."""
    task = asyncio.create_task(device.session(client))
    await asyncio.sleep(0)
    act()
    await asyncio.sleep(settle_s)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    return client.published


def readings_of(published, msg_type):
    return [p for p in published if p["message"].get("type") == msg_type]


# --- 1, 9, 10: topics, QoS and retain --------------------------------------------------


@pytest.mark.parametrize(
    ("make", "msg_type", "topic", "qos", "retain"),
    [
        (zone_device, "ZONE_TELEMETRY", "farm/siet-ph1/zone/Z1/telemetry", 0, False),
        (climate_device, "CLIMATE_TELEMETRY", "farm/siet-ph1/climate/telemetry", 0, False),
        (power_device, "POWER_STATE", "farm/siet-ph1/power/state", 1, True),
    ],
)
def test_each_reading_uses_its_contract_topic_qos_and_retain(farm, make, msg_type, topic, qos,
                                                             retain):
    device = make(farm)
    sent = asyncio.run(run_session(device, FakeClient(), act=lambda: device.take_reading(REAL)))
    [reading] = readings_of(sent, msg_type)
    assert (reading["topic"], reading["qos"], reading["retain"]) == (topic, qos, retain)


# --- 5, 6, 7, 8: the envelope ----------------------------------------------------------


@pytest.mark.parametrize(
    ("make", "msg_type", "device_id", "boot_id"),
    [
        (zone_device, "ZONE_TELEMETRY", "ZC1", 7),
        (climate_device, "CLIMATE_TELEMETRY", "CC1", 3),
        (power_device, "POWER_STATE", "PM1", 2),
    ],
)
def test_every_reading_has_the_full_envelope_and_passes_track_1s_model(farm, make, msg_type,
                                                                       device_id, boot_id):
    device = make(farm)
    sent = asyncio.run(run_session(device, FakeClient(), act=lambda: device.take_reading(REAL)))
    message = readings_of(sent, msg_type)[0]["message"]
    assert set(message) >= ENVELOPE_KEYS
    assert (message["v"], message["type"]) == (1, msg_type)
    assert (message["site"], message["device"], message["bootId"]) == ("siet-ph1", device_id,
                                                                       boot_id)
    assert uuid.UUID(message["msgId"]).version == 4
    assert message["ts"] == "2026-10-29T09:00:00.000Z"
    parse_farm_message(message)


def test_seq_counts_every_message_of_the_boot_in_publish_order(farm):
    device = zone_device(farm, heartbeat_s=0.005)

    async def scenario():
        client = FakeClient()
        task = asyncio.create_task(device.session(client))
        for _ in range(10):
            device.take_reading(REAL)
            await asyncio.sleep(0.003)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return client.published

    sent = [p["message"] for p in asyncio.run(scenario()) if "seq" in p["message"]]
    assert {m["type"] for m in sent} == {"DEVICE_STATUS", "ZONE_STATE", "ZONE_TELEMETRY"}
    assert [m["seq"] for m in sent] == list(range(1, len(sent) + 1))
    assert len({m["msgId"] for m in sent}) == len(sent)
    assert {m["bootId"] for m in sent} == {7}


def test_two_devices_count_seq_on_their_own(farm):
    zone, climate = zone_device(farm), climate_device(farm)
    zone.take_reading(REAL)
    climate.take_reading(REAL)
    zone_sent = asyncio.run(run_session(zone, FakeClient()))
    climate_sent = asyncio.run(run_session(climate, FakeClient()))
    assert readings_of(zone_sent, "ZONE_STATE")[0]["message"]["seq"] == 1
    assert readings_of(climate_sent, "CLIMATE_STATE")[0]["message"]["seq"] == 1
    assert readings_of(zone_sent, "ZONE_TELEMETRY")[0]["message"]["seq"] == 2
    assert readings_of(climate_sent, "CLIMATE_TELEMETRY")[0]["message"]["seq"] == 2


def test_a_new_boot_id_is_carried_by_every_message(farm):
    device = zone_device(farm, boot_id=413)
    sent = asyncio.run(run_session(device, FakeClient(), act=lambda: device.take_reading(REAL)))
    assert {p["message"]["bootId"] for p in sent if "bootId" in p["message"]} == {413}


def test_ts_is_real_time_and_simTs_is_the_readings_simulated_time_on_playground_sites(farm):
    clock = SimClock(SIM_START, 288, SPEEDS, monotonic=lambda: 0.0)
    device = zone_device(farm, clock=clock)
    due = SIM_START + timedelta(hours=5)
    sent = asyncio.run(run_session(device, FakeClient(), act=lambda: device.take_reading(due)))
    message = readings_of(sent, "ZONE_TELEMETRY")[0]["message"]
    assert message["ts"] == "2026-10-29T09:00:00.000Z"
    assert message["simTs"] == "2026-07-01T11:00:00.000Z"


def test_no_simTs_on_a_site_that_is_not_a_playground(farm):
    device = zone_device(farm, clock=None)
    sent = asyncio.run(run_session(device, FakeClient(), act=lambda: device.take_reading(REAL)))
    assert "simTs" not in readings_of(sent, "ZONE_TELEMETRY")[0]["message"]


def test_a_reading_keeps_the_time_and_values_it_was_taken_with(farm):
    # It may wait in the outbox for the broker; it must still say what was true when taken.
    device = zone_device(farm)
    device.take_reading(REAL)
    zone = farm.zones["Z1"]
    zone.reservoir = ReservoirState(volume_l=100.0, nutrient_g=100.0, ph=7.0, temp_c=30.0)
    sent = asyncio.run(run_session(device, FakeClient()))
    assert readings_of(sent, "ZONE_TELEMETRY")[0]["message"]["readings"]["reservoirL"] == 936


# --- 12, 13: alarms through a real device and tick loop --------------------------------


def alarm_rig(farm, speed=60):
    real = {"s": 0.0}
    clock = SimClock(SIM_START, speed, SPEEDS, monotonic=lambda: real["s"])
    device = zone_device(farm, clock=clock)
    return TickLoop(clock, farm, [device]), device, real


def outbox_messages(device):
    """The readings waiting in the outbox; the state messages are test_state_publishing's."""
    messages = [item.build() for item in device._outbox]
    device._outbox.clear()
    return [m for m in messages if m["type"] not in ("ZONE_STATE", "CLIMATE_STATE")]


def test_crossing_an_alarm_sends_a_reading_at_once_and_only_once(farm):
    loop, device, real = alarm_rig(farm)
    loop.tick()
    assert len(outbox_messages(device)) == 1  # the reading at the start
    zone = farm.zones["Z1"]

    zone.reservoir = ReservoirState(zone.reservoir.volume_l, zone.reservoir.nutrient_g,
                                    zone.reservoir.ph, 26.5)
    real["s"] += 0.05  # 3 simulated s: nowhere near the next reading
    loop.tick()
    urgent = outbox_messages(device)
    assert [m["type"] for m in urgent] == ["ZONE_TELEMETRY"]
    assert urgent[0]["readings"]["solTempC"] == 26.5
    assert urgent[0]["simTs"] == "2026-07-01T06:00:03.000Z"  # off the grid: out of turn

    for _ in range(5):  # still beyond the limit: nothing more until the next periodic reading
        real["s"] += 0.05
        loop.tick()
    assert outbox_messages(device) == []


def test_an_alarm_reading_does_not_restart_the_periodic_schedule(farm):
    loop, device, real = alarm_rig(farm)
    loop.tick()
    outbox_messages(device)
    zone = farm.zones["Z1"]
    zone.reservoir = ReservoirState(zone.reservoir.volume_l, zone.reservoir.nutrient_g,
                                    zone.reservoir.ph, 26.5)
    real["s"] += 0.5  # 30 simulated s: the alarm reading goes out here
    loop.tick()
    real["s"] += 0.5  # 60 simulated s: the periodic one is still due now, not 30 s later
    loop.tick()
    assert [m["simTs"] for m in outbox_messages(device)] == [
        "2026-07-01T06:00:30.000Z", "2026-07-01T06:01:00.000Z",
    ]


@pytest.mark.parametrize(
    ("change", "reading", "value"),
    [
        (lambda z: z.outputs.update({"Z1-CIRC": "OFF"}), "circFlowLpm", 0.0),
        (lambda z: z.day_tank_l.update(ACID=5.0), "dayTankPct", {"A": 64, "B": 66, "ACID": 10}),
        (lambda z: setattr(z, "reservoir", ReservoirState(240.0, 400.0, 6.0, 23.0)),
         "reservoirPct", 20),
    ],
)
def test_every_configured_zone_limit_triggers_a_reading(farm, change, reading, value):
    loop, device, real = alarm_rig(farm)
    loop.tick()
    outbox_messages(device)
    change(farm.zones["Z1"])
    real["s"] += 0.05
    loop.tick()
    [message] = outbox_messages(device)
    assert message["readings"][reading] == value


def test_a_value_the_physics_carries_over_the_limit_triggers_one_reading(farm):
    # No test hand: the solution warms toward the 31.2 C air and crosses solTempMaxC by itself.
    loop, device, real = alarm_rig(farm, speed=288)
    loop.tick()
    device._outbox.clear()
    messages = []
    for _ in range(7000):  # 10 ticks a real second: about 5.6 simulated hours
        real["s"] += 0.1
        loop.tick()
        messages += outbox_messages(device)
    temps = [m["readings"]["solTempC"] for m in messages]
    off_grid = [m for m in messages if not m["simTs"].endswith(":00.000Z")]
    assert temps[0] < 26.0 < temps[-1]
    assert len(off_grid) == 1
    assert off_grid[0]["readings"]["solTempC"] > 26.0


def test_climate_alarm_comes_from_the_air_setpoints(farm):
    clock = SimClock(SIM_START, 60, SPEEDS, monotonic=lambda: 0.0)
    device = climate_device(farm, clock=clock)
    device.take_reading(SIM_START)  # 31.2 C is already over 30.0 C: no crossing yet
    device._outbox.clear()
    device.take_urgent_reading(SIM_START)
    assert not device._outbox
    air = farm.climate.air
    farm.climate.air = type(air)(air.temp_by_pos_c, 90, air.outside_temp_c, air.outside_rh,
                                 air.elec_room_temp_c)
    device.take_urgent_reading(SIM_START)
    assert [m["readings"]["airRh"] for m in outbox_messages(device)] == [90]


# --- 3, 4: power --------------------------------------------------------------------


def test_power_state_goes_out_on_change_not_otherwise(farm):
    device = power_device(farm)
    device.take_reading(REAL)
    outbox_messages(device)
    device.take_urgent_reading(REAL)
    assert outbox_messages(device) == []

    farm.power.mains, farm.power.source = "LOST", "UPS_ONLY"
    farm.power.ups_on_battery = True
    device.take_urgent_reading(REAL)
    [message] = outbox_messages(device)
    assert (message["mains"], message["source"]) == ("LOST", "UPS_ONLY")
    assert message["ups"]["onBattery"] is True
    device.take_urgent_reading(REAL)
    assert outbox_messages(device) == []


def test_a_missing_phase_voltage_is_null_never_zero(farm):
    device = power_device(farm)
    farm.power.phases_v["l2V"] = None
    device.take_reading(REAL)
    [message] = outbox_messages(device)
    assert message["phases"]["l2V"] is None
    parse_farm_message(message)


def test_power_state_is_sent_again_when_the_link_comes_back(farm):
    # The dev broker keeps no retained messages over a restart.
    device = power_device(farm)
    device.take_reading(REAL)
    asyncio.run(run_session(device, FakeClient()))
    second = asyncio.run(run_session(device, FakeClient()))
    assert len(readings_of(second, "POWER_STATE")) == 1


def test_telemetry_is_not_sent_again_on_reconnect(farm):
    device = zone_device(farm)
    device.take_reading(REAL)
    asyncio.run(run_session(device, FakeClient()))
    second = asyncio.run(run_session(device, FakeClient()))
    assert readings_of(second, "ZONE_TELEMETRY") == []


def test_no_second_power_state_at_the_first_connection(farm):
    device = power_device(farm)
    device.take_reading(REAL)
    assert len(readings_of(asyncio.run(run_session(device, FakeClient())), "POWER_STATE")) == 1


# --- 19: publication failures ----------------------------------------------------------


def test_a_failed_publish_ends_the_session_so_connect_forever_reconnects(farm):
    device = zone_device(farm)
    device.take_reading(REAL)

    async def scenario():
        await device.session(FakeClient(fail_on=1))

    with pytest.raises(aiomqtt.MqttError):
        asyncio.run(scenario())


def test_a_failed_publish_is_sent_again_as_the_same_message(farm):
    device = zone_device(farm)
    device.take_reading(REAL)
    with pytest.raises(aiomqtt.MqttError):
        asyncio.run(device.session(FakeClient(fail_on=1)))
    kept = json.loads(device._in_flight.payload)
    assert kept["type"] == "ZONE_STATE"

    sent = [p["message"] for p in asyncio.run(run_session(device, FakeClient()))]
    assert sent[0] == kept  # same msgId and seq, so the back end can drop a duplicate
    assert [m["seq"] for m in sent if "seq" in m][:2] == [1, 2]  # and the count carries on
    assert sent[1]["type"] == "ZONE_TELEMETRY"


def test_a_failed_publish_does_not_stop_later_readings_or_leave_a_seq_gap(farm):
    device = zone_device(farm)

    async def failing_session():
        task = asyncio.create_task(device.session(FakeClient(fail_on=3)))
        await asyncio.sleep(0)
        for _ in range(4):
            device.take_reading(REAL)
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(failing_session())
    device.take_reading(REAL)
    sent = asyncio.run(run_session(device, FakeClient()))
    seqs = [p["message"]["seq"] for p in sent if "seq" in p["message"]]
    assert seqs == sorted(seqs) and seqs == list(range(seqs[0], seqs[0] + len(seqs)))


def test_readings_taken_before_the_first_connection_go_out_in_order(farm):
    device = zone_device(farm)
    for minute in range(3):
        device.take_reading(SIM_START + timedelta(minutes=minute))
    sent = readings_of(asyncio.run(run_session(device, FakeClient())), "ZONE_TELEMETRY")
    seqs = [p["message"]["seq"] for p in sent]
    assert len(seqs) == 3 and seqs == sorted(seqs)


def test_a_long_outage_keeps_the_newest_readings_and_says_so(farm, caplog):
    device = zone_device(farm)
    with caplog.at_level(logging.WARNING, logger="sim.devices"):
        for _ in range(OUTBOX_MAX + 10):
            device.take_reading(REAL)
    assert len(device._outbox) == OUTBOX_MAX
    assert caplog.text.count("outbox full") == 1
    sent = asyncio.run(run_session(device, FakeClient()))
    seqs = [p["message"]["seq"] for p in sent if "seq" in p["message"]]
    assert seqs == list(range(1, OUTBOX_MAX + 1))  # what was dropped never got a seq: no gaps


# --- 14, 15, 18: heartbeats are untouched ----------------------------------------------


def test_heartbeats_keep_flowing_alongside_readings_while_the_clock_is_paused(farm):
    clock = SimClock(SIM_START, 0, SPEEDS)
    device = zone_device(farm, clock=clock, heartbeat_s=0.01)
    loop = TickLoop(clock, farm, [device])

    async def scenario():
        client = FakeClient()
        task = asyncio.create_task(device.session(client))
        for _ in range(10):
            loop.tick()
            await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return client.published

    sent = asyncio.run(scenario())
    assert len(readings_of(sent, "ZONE_TELEMETRY")) == 1  # the one at the start
    beats = [p for p in sent if p["message"].get("online")]
    assert len(beats) >= 3
    assert all(p["qos"] == 1 and p["retain"] is True for p in beats)
    assert all(p["topic"] == "farm/siet-ph1/device/ZC1/status" for p in beats)


def test_a_clean_stop_still_ends_with_the_offline_status(farm):
    device = zone_device(farm)
    sent = asyncio.run(run_session(device, FakeClient(), act=lambda: device.take_reading(REAL)))
    assert sent[-1]["message"] == {"v": 1, "type": "DEVICE_STATUS", "device": "ZC1",
                                   "online": False}


# --- 2, 17: devices come from the layout -------------------------------------------------


def client_a_devices(farm):
    alerts = read_pack_file(CLIENT_A, "alerts.json")
    site = load_site(CLIENT_A)
    return {
        entry["id"]: make_device(entry, site, 1, farm, alerts, None)
        for entry in load_devices(CLIENT_A)
    }


def test_each_layout_entry_becomes_the_right_kind_of_device(farm):
    devices = client_a_devices(farm)
    assert type(devices["GW1"]) is SimDevice
    assert isinstance(devices["ZC1"], ZoneDevice)
    assert isinstance(devices["CC1"], ClimateDevice)
    assert isinstance(devices["PM1"], PowerDevice)
    assert devices["ZC1"].zone is farm.zones["Z1"]
    assert devices["ZC2"].zone is farm.zones["Z2"]
    assert devices["ZC2"].reading_topic == "farm/siet-ph1/zone/Z2/telemetry"


def test_the_gateway_sends_no_readings(farm):
    assert not isinstance(client_a_devices(farm)["GW1"], ReadingDevice)


def test_a_zone_controller_for_a_zone_not_in_the_layout_is_refused(farm):
    entry = {"id": "ZC9", "kind": "ZONE_CONTROLLER", "zone": "Z9"}
    with pytest.raises(ValueError, match="ZC9"):
        make_device(entry, "siet-ph1", 1, farm, {}, None)


def test_every_zone_device_watches_its_own_zones_limits(farm):
    alerts = {"zones": {"Z1": {"alarms": {"solTempMaxC": 26.0}},
                        "Z2": {"alarms": {"solTempMaxC": 40.0}}}}
    entry = {"id": "ZC2", "kind": "ZONE_CONTROLLER", "zone": "Z2"}
    device = make_device(entry, "siet-ph1", 1, farm, alerts, None)
    device.take_reading(REAL)
    device._outbox.clear()
    zone = farm.zones["Z2"]
    zone.reservoir = ReservoirState(zone.reservoir.volume_l, zone.reservoir.nutrient_g,
                                    zone.reservoir.ph, 30.0)
    device.take_urgent_reading(REAL)
    assert not device._outbox  # 30 C is fine for Z2's 40 C limit
    zone.reservoir = ReservoirState(zone.reservoir.volume_l, zone.reservoir.nutrient_g,
                                    zone.reservoir.ph, 41.0)
    device.take_urgent_reading(REAL)
    assert len(outbox_messages(device)) == 1
