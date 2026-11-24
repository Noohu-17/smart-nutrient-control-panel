# Infrastructure

- `docker-compose.dev.yml`, `mosquitto/mosquitto.conf`: the dev stack. Its broker has no TLS and no passwords, and listens on 127.0.0.1 only. Nothing below changes it.
- `docker-compose.staging.yml`, `mosquitto/staging/`, `caddy/`: the staging stack. Steps: [README.md](../README.md), "Deploy to staging".

## The staging broker (T1-36)

| Port | Who | How |
|---|---|---|
| 1883 | The API (`CLOUD-api`), the worker (`CLOUD-worker`), the simulator | Plain MQTT on the internal network only, never published |
| 8883 | The farm's gateway, from outside | MQTT over TLS, published. Only the real devices' logins from `add-device-login.sh`: the simulator's logins (`MQTT_PASSWORD_*`) and `CLOUD-*` accounts are refused here |

Every client logs in with its own account, and [acl.conf](mosquitto/staging/acl.conf) says what each may publish and read (contract 3 and 4). Anything not listed is refused.

The broker accepts packets up to 64 KB, four times the contract's 16 KB message limit, and disconnects a client that sends a larger one. Mosquitto applies this limit to both ports; it can't be set for 8883 alone.

`GW1` relays for the farm's devices. It may publish exactly what `ZC1`, `ZC2`, `CC1` and `PM1` publish, plus its own status and `gateway/journal`. It may read exactly their commands, `stop` and their config. Every topic is listed, with no wildcards, so it can't publish for a zone or a device that isn't in the layout. Its MQTT bridge may subscribe with wildcards such as `zone/+/cmd`: the broker accepts the subscription but gives the gateway only the topics it may read.

[start.sh](mosquitto/staging/start.sh) builds the broker's logins and its TLS listener each time the broker starts. These files are kept in `mosquitto/staging/secrets/`, which git ignores:

| File | What | Made by |
|---|---|---|
| `passwords` | The farm devices' logins, as hashes | `add-device-login.sh` |
| `server.crt`, `server.key` | The broker's certificate and its key | `make-broker-cert.sh server` |

Without `server.crt` and `server.key` the broker logs a warning and runs without 8883. The rest of the stack works as before.

## The certificate: the project's own CA

The gateway checks the broker's certificate against the project's own CA, not a public one. Only a certificate this CA signed is accepted.

| File | Secret? | Where it lives |
|---|---|---|
| `ca.key` | **Yes**: whoever has it can pose as the broker to every gateway | The password manager or offline. Never on the server, never in git |
| `ca.crt` | No | Built into the gateway's firmware, and on any laptop that runs the check |
| `server.key` | **Yes** | The server's `mosquitto/staging/secrets/` only |
| `server.crt` | No | Next to `server.key` |

What the broker's certificate must have (`make-broker-cert.sh` does all of this):

- It is signed by the project CA. The CA lasts 10 years and the broker certificate 2 years.
- Its names (subjectAltName) list every name or IP address the gateway connects to: the server's domain on staging, `localhost` and `127.0.0.1` on a laptop. A gateway connecting by any other name is refused.
- It is for a server only: `serverAuth`, not a CA.
- Keys are RSA 2048, which embedded TLS stacks accept.
- The broker accepts TLS 1.2 or newer.

### Make the CA, once for the team

Run from the repo root (Git Bash on Windows, which has openssl; OpenSSL 1.1.1 and 3.x both work):

```
sh infra/mosquitto/make-broker-cert.sh ca
```

It writes `infra/mosquitto/ca/ca.key` and `ca.crt`, after checking the CA: each extension once (mbedTLS on the gateway refuses a repeated one), `CA:TRUE, pathlen:0`, and that it verifies. If a check fails, it prints why and saves nothing. Put both in the password manager. Give `ca.crt` to the gateway's firmware owner. Don't run it again: a new CA means every gateway needs the new `ca.crt`. Anyone who makes a broker certificate later first puts the team's `ca.crt` and `ca.key` from the password manager into `infra/mosquitto/ca/`.

### Make the broker's certificate

```
sh infra/mosquitto/make-broker-cert.sh server staging.example.org     # the server's domain
sh infra/mosquitto/make-broker-cert.sh server localhost 127.0.0.1     # a laptop
```

It writes `infra/mosquitto/staging/secrets/server.crt` and `server.key`, then prints the names and the end date. Make it on a laptop that has the CA, then copy `server.crt` and `server.key` to the server's `infra/mosquitto/staging/secrets/`. `ca.key` stays off the server. Then restart the broker:

```
docker compose --env-file infra/.env.staging -f infra/docker-compose.staging.yml restart mqtt
```

The broker's log says `TLS listener on 8883`.

### Renew it

The broker certificate lasts 2 years. Put the end date it prints in the team calendar, with a reminder a month before. To renew it, make a new one with the same command and restart the broker. The gateways need nothing new, because the CA stays the same.

## Device logins

A real farm device gets its login from the script, never from git:

```
sh infra/mosquitto/add-device-login.sh GW1
```

- It makes a random password, keeps only its hash in `secrets/passwords`, and prints the password once. Put it in the password manager, then in the device.
- It needs Docker, because it uses the broker's own `mosquitto_passwd`.
- It refuses a device with no block in `acl.conf`, and it refuses `CLOUD-*` accounts. The back end's passwords stay in `infra/.env.staging`.
- Restart the broker afterwards, as above.

A client set up through the API (contract 11.5, T1-40) gets its devices' logins when the setup is saved: the passwords are answered once, and the back end keeps their hashes in the same format. `python -m app.setup.logins <org>`, from `backend/`, prints the lines to add to `secrets/passwords`. The new site's devices also need their blocks in `acl.conf`; then restart the broker.

**One broker per client.** The MQTT username is the device ID (contract 3), and every client uses the same device IDs (`ZC1`, `CC1`, `PM1` …). So the lines go into the **new client's own broker**, the one in its own deployment (its own `ORG_ID`, `secrets/passwords` and `acl.conf`), never into another client's: there client_C's `ZC1` would replace client_A's `ZC1` and could use client_A's topics.

Only logins made by the script are accepted on 8883. The `MQTT_PASSWORD_<ID>` logins in `infra/.env.staging` are the simulator's and work on 1883 only, inside the server, so whoever holds one can't send readings from the internet.

A login made by the script also replaces that device's `MQTT_PASSWORD_<ID>` on 1883. So once the real `GW1` has its login, the simulator can no longer log in as `GW1`: the two never share the account. Don't run the simulator's `GW1` while the real gateway is connected. The other devices keep their simulator logins until they get their own.

## Check it on a laptop

This checks that the gateway's login can publish its topics and nothing else, and that 1883 isn't published.

1. Make a local CA and broker certificate. These are for this laptop only; never use them on a server:

   ```
   sh infra/mosquitto/make-broker-cert.sh ca
   sh infra/mosquitto/make-broker-cert.sh server localhost 127.0.0.1
   ```

2. With `infra/.env.staging` filled in (README.md, "Deploy to staging", step 1), start the broker alone:

   ```
   docker compose --env-file infra/.env.staging -f infra/docker-compose.staging.yml up -d mqtt
   ```

3. Give `GW1` a login for 8883, and restart the broker so it reads it:

   ```
   sh infra/mosquitto/add-device-login.sh GW1
   docker compose --env-file infra/.env.staging -f infra/docker-compose.staging.yml restart mqtt
   ```

4. Run the check with the backend's virtual environment, which has paho-mqtt, giving it the password the script printed for `GW1`:

   ```
   MQTT_PASSWORD_GW1='<printed password>' backend/.venv/Scripts/python scripts/broker-acl-check.py --env-file infra/.env.staging
   ```

   It prints PASS or FAIL for each check:
   - the ports;
   - that the certificate is trusted only through the CA;
   - that anonymous logins, wrong passwords, `CLOUD-worker` and `ZC1` with the simulator's password are refused on 8883;
   - every topic GW1 may publish and read, and the forbidden ones;
   - that a message over 64 KB is refused on 8883.

   The broker acknowledges a publish its ACL drops, and it accepts any subscription and filters each message instead. So the check looks at what really arrives, using a `CLOUD-worker` subscriber and a `CLOUD-api` publisher inside the broker's container. It publishes test messages on the farm's topics, commands included, so run it only on a laptop, never on a site in use.

5. Stop the broker with `docker compose ... stop mqtt`.

## client_B and the prototype rig

client_B's site is `coimbatore-ph1`, so its broker uses [acl-coimbatore-ph1.conf](mosquitto/staging/acl-coimbatore-ph1.conf) instead of `acl.conf` (client_A's `siet-ph1`). The same blocks, for its own site, without `GW1`: client_B's farm link is the rig's own gateway on its `sf/*` topics.

The zone controller service (`zone-controller` in the staging compose file, T1-32) runs the zones `io_map.json` puts on the rig. It logs in on 1883 only: as the zone's controller (`ZC1`) for the contract topics, and as `RIGLINK` for the rig's `sf/*` topics. With client_A it exits at once and stays stopped.

In `infra/.env.staging` for client_B:

| Setting | Value |
|---|---|
| `CLIENT_DIR` | `/config/clients/client_B` |
| `MQTT_ACL` | `acl-coimbatore-ph1.conf` |
| `RIG_TELEMETRY_TOPIC`, `RIG_COMMAND_TOPIC` | The gateway's topic bases, `m/<workspace>/c/<channel>`, from its `secrets.h`. Never in git |
| `MQTT_PASSWORD_RIGLINK` | `openssl rand -hex 24`; in the password manager too |
| `RIG_GATEWAY_LOGIN` | Only if the gateway's MQTT username isn't `gateway-01` |

Each time it starts, [start.sh](mosquitto/staging/start.sh) writes the broker's ACL from the `MQTT_ACL` file, then adds two blocks when the `RIG_*` topics are set: the gateway may publish on `<telemetry>/sf/#` and read `<command>/sf/cmd`; `RIGLINK` the reverse. Nothing else: no wildcards beyond the gateway's own `sf/#`, and no access to the contract's `farm/...` topics. Empty `RIG_*` values mean no rig and no rig accounts; an empty `MQTT_PASSWORD_*` makes no account.

The gateway's own login, for 8883: `MQTT_ACL=acl-coimbatore-ph1.conf sh infra/mosquitto/add-device-login.sh gateway-01`, then restart the broker. The printed password goes into the password manager, and from there into the gateway's `secrets.h`.

## Not covered yet

- `scripts/broker-acl-check.py` checks `acl.conf` (client_A's `GW1`) only, not the rig's two blocks.
