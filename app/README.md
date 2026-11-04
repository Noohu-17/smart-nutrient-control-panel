PWA for Owner, Agronomist, Viewer and Engineer roles.

## Fake API (MSW, T2-4)

`npm run dev` starts a fake back end inside the browser (`src/mocks/`). It answers:

- `GET /api/health` → `{ "status": "OK", "database": "UP", "broker": "UP" }` (503 `DEGRADED` with `?mock=api-degraded`)
- `GET /api/farm` → `src/mocks/data/layout.json` plus `features` from `src/mocks/data/features.json`, copies of the same files in `config/clients/client_A/` (a test fails if they differ)
- `GET /api/zones`, `/api/zones/Z1`, `/api/zones/Z2` → the contract §11.2 zone response, as the real back end answers it; readings move a little on every request

Add `?mock=<name>` to the URL to see other states (kept for the tab; `?mock=normal` to go back):
`z2-offline`, `z2-sensor-fault`, `z2-stale`, `z1-estop`, `farm-error`, `no-playground`, `slow`. See `src/mocks/scenario.ts`.

## Real back end (T2-12)

Node 26 (`.nvmrc` at the repo root). Start the dev stack with the API and the simulator (`backend/README.md`), then:

```
# PowerShell
$env:VITE_USE_MOCKS = "false"; npm run dev
# bash
VITE_USE_MOCKS=false npm run dev
```

Or put `VITE_USE_MOCKS=false` in `app/.env.local` (not committed). Vite then sends `/api` to `http://localhost:8000`; set `VITE_API_TARGET` for another address. Leave `VITE_USE_MOCKS` unset (or `true`) for the fake API. Tests always use the fake API.
