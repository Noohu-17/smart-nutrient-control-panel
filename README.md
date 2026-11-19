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

## Deploy to staging

`infra/docker-compose.staging.yml` runs Caddy (HTTPS, the app and `/api`), the API, the worker, the database, the broker and the simulator. Published ports: Caddy's (80 and 443) and the broker's TLS port for the farm's gateway (8883). The database and the broker's plain port (1883) are internal only. On a server, the domain's DNS must point at it and ports 80, 443 and 8883 must be open. To try it on a laptop, keep `SITE_ADDRESS=localhost`.

Before the farm's gateway can connect, the broker needs its certificate and the gateway its login: [infra/readme.md](infra/readme.md). Without the certificate the broker runs without 8883, and the rest of the stack works as before.

1. Copy `infra/.env.example` to `infra/.env.staging` (git ignores it) and fill in every blank password. On a server, set `SITE_ADDRESS` to the domain.

   Set the site's login too. Choose `SITE_USER`, then make the password's hash. This asks for the password twice and prints the hash:

   ```
   docker run --rm -it caddy:2.10.2-alpine caddy hash-password
   ```

   Paste it into `SITE_PASSWORD_HASH` between single quotes, so its `$` signs stay as they are: `SITE_PASSWORD_HASH='$2a$14$...'`. Only the hash goes in the file. Give the password itself to people separately.

   `VITE_DEMO_MODE` (`true` or `false`) is built into the app; the app reads it from T2-17 on.
2. Start it from the repo root. The first `up` also creates the database. Every `up` runs the migrations and loads the client's `layout.json`, then starts the rest:

   ```
   docker compose --env-file infra/.env.staging -f infra/docker-compose.staging.yml up -d --build
   ```

3. Check it. Every service should say `healthy`, and health should answer `{"status":"OK","database":"UP","broker":"UP"}`. The whole site asks for the login, so health needs it too, and curl asks for the password:

   ```
   docker compose --env-file infra/.env.staging -f infra/docker-compose.staging.yml ps
   curl -u <SITE_USER> https://<SITE_ADDRESS>/api/health
   ```

   On `localhost`, Caddy uses its own certificate, so add `-k` to curl and accept the browser's warning. Without the login every address answers `401`.

4. Run the demo check, after every deploy and again before the demo. It plays the demo through the API (a dose, Stop and Resume, a blocked dose, a sensor fault) and prints PASS or FAIL for each step, then puts the demo farm back with Reset. The password goes in an environment variable, never on the command line:

   ```
   $env:DEMO_CHECK_PASSWORD = "<the site password>"          # PowerShell; export DEMO_CHECK_PASSWORD=... in bash
   python scripts/demo-check.py https://<SITE_ADDRESS> --user <SITE_USER> --speed 60
   ```

   `--speed 60` makes it take about a minute; Reset puts the start speed back. On `localhost` add `--insecure`, for the same certificate reason as `-k`. Against the dev stack it needs no login: `python scripts/demo-check.py http://localhost:8000 --speed 60`. On a site without the playground it runs only the health and readings checks, so it never doses a real farm.

Stop it with `down` in place of `up -d --build`. Data stays in Docker volumes (database, broker, simulator `.state`, certificates); `down -v` deletes all of it. The broker's logins and certificate are files in `infra/mosquitto/staging/secrets/`, not volumes, so `down -v` keeps them.

Changing `POSTGRES_PASSWORD` later needs an `ALTER USER` in the database too, because the value only takes effect when the database is first created.

To change the site's login, put the new `SITE_USER` or hash in `infra/.env.staging` and run `up -d caddy` in place of `up -d --build`. Changing `VITE_DEMO_MODE` needs `up -d --build caddy`, because it is built into the app.
