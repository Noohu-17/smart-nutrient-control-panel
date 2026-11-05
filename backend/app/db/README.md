# Database

PostgreSQL with TimescaleDB (the dev stack's `db` service). SQLAlchemy models are in `models.py`, migrations are in `backend/alembic/`, and `seed.py` loads the farm layout.

| Table | Holds | Filled by |
| :--- | :--- | :--- |
| `site` | the site: ID, name, time zone | the seed, from `layout.json` |
| `zone` | zones: reservoir, dosing cabinet, dose lines, day-tanks, optional sensors | the seed |
| `bench` | benches and their channels | the seed |
| `device` | devices that connect to MQTT (GW1, ZC1 …) and a zone controller's zone; `last_seen` and `last_will_at` | the seed; the worker sets `last_seen` and `last_will_at`, which the seed keeps |
| `reading` | sensor readings, one row per value; a TimescaleDB hypertable on `ts` | the worker |
| `latest_message` | each device's newest `ZONE_STATE`, `CLIMATE_STATE`, `ZONE_TELEMETRY` and `POWER_STATE`, one row each; replaced only by a newer (`bootId`, `seq`) | the worker |
| `app_user` | people who sign in (`user` is reserved in PostgreSQL) | the sign-in code (later task) |

Every table has `org_id` and `site_id` (OPEN-11). Rows are keyed by the contract's IDs (`siet-ph1`, `Z1`, `Z1-B1`, `ZC1`). Until the organisation layer exists, `org_id` is the client's config-pack folder name (`client_A`).

## Build and seed the database

On the dev stack, from the repo root:

```
docker compose -f infra/docker-compose.dev.yml up -d --build
docker compose -f infra/docker-compose.dev.yml exec api alembic upgrade head
docker compose -f infra/docker-compose.dev.yml exec api python -m app.db.seed
```

Or from `backend/` on the host, with the dev stack running:

```
.venv/Scripts/alembic upgrade head
.venv/Scripts/python -m app.db.seed                             # CLIENT_DIR, default config/clients/client_A
.venv/Scripts/python -m app.db.seed ../config/clients/client_B  # another client
```

The seed makes the database match `layout.json`. It adds or updates rows, and removes zones, benches and devices that are no longer in the file, so running it twice changes nothing. No farm value is written in code. Check the pack first with `python scripts/validate_config.py`.

Not loaded yet: the `climate`, `power` and `nursery` parts of `layout.json` have no tables in this first schema, and users are added by the sign-in task, not from `users.json`.

## Changing the schema

Change `models.py`, then from `backend/` run `alembic revision --autogenerate -m "what changed"`. Review the generated file, and add TimescaleDB steps (such as `create_hypertable`) by hand. `tests/test_db_schema.py` fails if the migrations and the models disagree.

## Tests

From `backend/`, with the dev stack's PostgreSQL running: `.venv/Scripts/python -m pytest`. Each database test creates its own throwaway database and drops it afterwards. Without PostgreSQL, the database tests are skipped.
