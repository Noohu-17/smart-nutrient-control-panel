"""Fixtures for the zone controller service's tests (T1-32); helpers in rig_support.py."""

from __future__ import annotations

import pytest
from rig_support import Clock, Rig, make_rig_pack


@pytest.fixture
def rig_pack(tmp_path):
    return make_rig_pack(tmp_path)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def rig(tmp_path, rig_pack, clock):
    return Rig(tmp_path, rig_pack, clock)
