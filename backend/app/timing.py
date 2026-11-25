"""Contract §14's timing constants, in one place for the API and the worker, so they can't drift
apart: the zone answer's `online` and STALE (api/status.py), the dose review (api/dosing.py) and
the silence alerts' defaults (alerts.py) all use these."""

from datetime import timedelta

OFFLINE_AFTER = timedelta(minutes=3)  # "Device offline: 3 minutes without any message" (§14)
FRESH_FOR = timedelta(seconds=120)  # "Fresh EC/pH to dose: ≤ 120 s old" (§14)
