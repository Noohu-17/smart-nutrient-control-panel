# Smart Nutrient Advisor — Phase 2
Remote nutrient dosing and climate monitoring for the Coimbatore polyhouse (2 NFT zones).

## Folders
- firmware/ — ESP32 controller code (zone + climate controllers)
- backend/  — cloud API, MQTT broker config, database
- app/      — PWA (Owner, Agronomist, Operator, Viewer, Engineer views)
- docs/     — scope, tracker, interface contract, wiring, site photos, decisions

## Checks (run before raising a PR)

One command runs every check: Python 3.13, back end and simulator (ruff + pytest), app (lint + tests). From the repo root:

```
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1
```

It ends with "All checks passed." or lists what failed. Paste the output in your PR.

Once per laptop: Python 3.13 (see `.python-version`) and the current Node LTS, then
- `backend/` and `simulator/`: create a virtual environment (`py -3.13 -m venv .venv`) and run `pip install -r requirements-dev.txt` inside it;
- `app/`: `npm ci`.

The back end's database tests run only when the dev stack is up (`docker compose -f infra/docker-compose.dev.yml up -d`); otherwise they show as skipped.
