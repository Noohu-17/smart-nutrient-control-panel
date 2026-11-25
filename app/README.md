PWA for Owner, Agronomist, Viewer and Engineer roles.

## Fake API (MSW, T2-4)

`npm run dev` starts a fake back end inside the browser (`src/mocks/`). It answers:

- `GET /api/health` → `{ "status": "OK", "database": "UP", "broker": "UP" }` (503 `DEGRADED` with `?mock=api-degraded`)
- `GET /api/farm` → `src/mocks/data/layout.json` plus `features` from `src/mocks/data/features.json`, copies of the same files in `config/clients/client_A/` (a test fails if they differ)
- `GET /api/zones`, `/api/zones/Z1`, `/api/zones/Z2` → the contract §11.2 zone response, as the real back end answers it; readings move a little on every request
- `/api/sim/…` (contract §11.4, playground only) → a fake simulator in `src/mocks/sim.ts`: `GET state` and `scenario-options` (a copy of `config/clients/client_A/scenarios.json`, checked by a test), `POST speed`, `faults`, `holds`, `holds/release-all`, `reset`, and `DELETE holds/{target}/{metric}`. It keeps the speed, the simulated clock, running drills and held values until the page reloads, and answers 422 to a value `scenarios.json` doesn't allow. A drill or held value doesn't change the fake readings yet

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

## Demo mode (T2-17)

`VITE_DEMO_MODE` decides whether the app says it is a demo:

| | Sign-in page | Every screen |
| :--- | :--- | :--- |
| On | Role picker (Owner, Agronomist, Operator, Viewer) | "Demo · simulated farm" banner at the top |
| Off | No picker: "Sign-in with your own account is coming soon" | No banner |

- **A build** (`npm run build`) is off unless built with `VITE_DEMO_MODE=true`. Staging (T1-19) builds with it.
- **`npm run dev`** is on, so the role picker works as before; `VITE_DEMO_MODE=false npm run dev` to see the off look.
- **Tests** run with it on; `src/app/DemoMode.test.tsx` switches it with `vi.stubEnv` to test both.
- Off, a role picked earlier in a demo build doesn't sign anyone in.
