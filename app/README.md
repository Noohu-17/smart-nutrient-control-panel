PWA for Owner, Agronomist, Viewer and Engineer roles.

## Fake API (MSW, T2-4)

`npm run dev` starts a fake back end inside the browser (`src/mocks/`). It answers:

- `GET /api/health` → `{ "status": "OK", "database": "UP", "broker": "UP" }` (503 `DEGRADED` with `?mock=api-degraded`)
- `GET /api/farm` → `src/mocks/data/layout.json` plus `features` from `src/mocks/data/features.json`, copies of the same files in `config/clients/client_A/` (a test fails if they differ)
- `GET /api/zones/Z1`, `/api/zones/Z2` → zone telemetry shaped like contract §6.1; readings move a little on every request

Add `?mock=<name>` to the URL to see other states (kept for the tab; `?mock=normal` to go back):
`z2-offline`, `z2-sensor-fault`, `z2-stale`, `z1-estop`, `farm-error`, `no-playground`, `slow`. See `src/mocks/scenario.ts`.
