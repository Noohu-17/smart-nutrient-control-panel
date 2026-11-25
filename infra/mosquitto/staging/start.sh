#!/bin/sh
# Builds the broker's password files and its TLS listener, then starts Mosquitto (infra/readme.md).
#
# Passwords: first the MQTT_PASSWORD_* variables. MQTT_PASSWORD_ZC1 becomes the account ZC1,
# MQTT_PASSWORD_CLOUD_API the account CLOUD-api. Then the device logins that
# infra/mosquitto/add-device-login.sh keeps in secrets/passwords: a device listed there keeps that
# password, not its variable's, so the real GW1 and the simulator's GW1 can't both log in.
# CLOUD-* accounts come only from the variables.
#
# Listeners: 1883 for every account, inside the server only. 8883, MQTT over TLS for the farm,
# accepts only the real devices' logins from secrets/passwords: never CLOUD-*, never the
# simulator's MQTT_PASSWORD_* logins. It needs secrets/server.crt and secrets/server.key;
# without them the broker logs a warning and runs on 1883 alone.
set -eu

secrets=/mosquitto/staging/secrets
passwords=/tmp/mosquitto-passwords            # every account: listener 1883
farm_passwords=/tmp/mosquitto-passwords-farm  # farm devices only: listener 8883
listeners=/tmp/mosquitto-listeners            # mosquitto.conf includes every *.conf here
tls=/tmp/mosquitto-tls
acl=/tmp/mosquitto-acl.conf                   # both listeners

# The ACL: the site's file (MQTT_ACL; acl.conf is client_A's siet-ph1), then, when the farm has
# the prototype rig, its gateway's and the zone controller service's blocks on the rig's sf/*
# topics. The topic bases are the gateway's own settings (m/<workspace>/c/<channel>), kept in
# infra/.env.staging, never in git.
acl_name=${MQTT_ACL:-acl.conf}
case "$acl_name" in
    */* | .* | '') echo "start.sh: MQTT_ACL must be a file name in infra/mosquitto/staging" >&2; exit 1 ;;
esac
if [ ! -f "/mosquitto/staging/$acl_name" ]; then
    echo "start.sh: no ACL file infra/mosquitto/staging/$acl_name (MQTT_ACL)" >&2
    exit 1
fi
rm -f "$acl"
tr -d '\r' < "/mosquitto/staging/$acl_name" > "$acl"
rig_telemetry=${RIG_TELEMETRY_TOPIC:-}
rig_command=${RIG_COMMAND_TOPIC:-}
rig_gateway=${RIG_GATEWAY_LOGIN:-gateway-01}
if [ -n "$rig_telemetry" ] || [ -n "$rig_command" ]; then
    for base in "$rig_telemetry" "$rig_command"; do
        case "$base" in
            '' | *[!A-Za-z0-9/_-]* | /* | */)
                echo "start.sh: RIG_TELEMETRY_TOPIC and RIG_COMMAND_TOPIC must both be set, as" \
                    "m/<workspace>/c/<channel> (letters, digits, / _ -; no wildcards)" >&2
                exit 1 ;;
        esac
    done
    case "$rig_gateway" in
        *[!A-Za-z0-9-]* | CLOUD-* | RIGLINK)
            echo "start.sh: RIG_GATEWAY_LOGIN '$rig_gateway' is not a device ID" >&2; exit 1 ;;
    esac
    cat >> "$acl" <<EOF

# Written by start.sh from RIG_TELEMETRY_TOPIC and RIG_COMMAND_TOPIC: the prototype rig (T1-32).
# Its gateway (a login from add-device-login.sh, so it may use 8883) publishes on sf/* and reads
# its commands; the zone controller service (RIGLINK, 1883 only) does the reverse.
user $rig_gateway
topic write $rig_telemetry/sf/#
topic read $rig_command/sf/cmd

user RIGLINK
topic read $rig_telemetry/sf/#
topic write $rig_command/sf/cmd
EOF
    echo "start.sh: rig accounts $rig_gateway and RIGLINK on the rig's sf/* topics" >&2
fi
chown mosquitto:mosquitto "$acl"
chmod 600 "$acl"

# Fresh files each start: a restarted container keeps /tmp, owned by mosquitto from the last run.
rm -f "$passwords" "$farm_passwords"
: > "$passwords"
chmod 600 "$passwords"

for variable in $(env | sed -n 's/^\(MQTT_PASSWORD_[A-Z0-9_]*\)=.*/\1/p'); do
    account=${variable#MQTT_PASSWORD_}
    case "$account" in
        CLOUD_*) account="CLOUD-$(echo "${account#CLOUD_}" | tr '[:upper:]' '[:lower:]')" ;;
    esac
    eval "password=\$$variable"
    if [ -z "$password" ]; then
        continue  # an empty value is no account (MQTT_PASSWORD_RIGLINK on a farm without the rig)
    fi
    mosquitto_passwd -b "$passwords" "$account" "$password"
done

# Device logins from add-device-login.sh: hashed lines, "account:hash".
if [ -f "$secrets/passwords" ]; then
    tr -d '\r' < "$secrets/passwords" | while IFS= read -r line || [ -n "$line" ]; do
        account=${line%%:*}
        case "$account" in
            '' | '#'*) continue ;;
            CLOUD-*)
                echo "start.sh: skipped $account in secrets/passwords: CLOUD accounts come from MQTT_PASSWORD_CLOUD_*" >&2
                continue ;;
            *[!A-Za-z0-9-]*)
                echo "start.sh: skipped a line in secrets/passwords: '$account' is not a device ID" >&2
                continue ;;
        esac
        grep -v "^$account:" "$passwords" > "$passwords.new" || true
        cat "$passwords.new" > "$passwords"
        rm -f "$passwords.new"
        printf '%s\n' "$line" >> "$passwords"
        echo "start.sh: $account logs in with its password from secrets/passwords" >&2
    done
fi

# 8883 takes only the real farm devices' logins, from secrets/passwords. The MQTT_PASSWORD_*
# variables are the simulator's: whoever holds one must not be able to log in from the internet.
: > "$farm_passwords"
chmod 600 "$farm_passwords"
if [ -f "$secrets/passwords" ]; then
    tr -d '\r' < "$secrets/passwords" | grep -E '^[A-Za-z0-9-]+:' | grep -v '^CLOUD-' \
        > "$farm_passwords" || true
fi
if [ ! -s "$farm_passwords" ]; then
    echo "start.sh: no device logins in secrets/passwords, so 8883 accepts nobody yet." \
        "infra/mosquitto/add-device-login.sh makes them." >&2
fi
chown mosquitto:mosquitto "$passwords" "$farm_passwords"

rm -rf "$listeners" "$tls"
mkdir -p "$listeners"
if [ -f "$secrets/server.crt" ] && [ -f "$secrets/server.key" ]; then
    mkdir -p "$tls"
    cp "$secrets/server.crt" "$secrets/server.key" "$tls/"
    chmod 600 "$tls/server.key"
    chown -R mosquitto:mosquitto "$tls"
    cat > "$listeners/tls.conf" <<EOF
# Written by start.sh: MQTT over TLS for the farm's devices, published by the compose file.
listener 8883
allow_anonymous false
password_file $farm_passwords
acl_file $acl
certfile $tls/server.crt
keyfile $tls/server.key
tls_version tlsv1.2
EOF
    echo "start.sh: TLS listener on 8883 with secrets/server.crt" >&2
else
    echo "start.sh: WARNING: no secrets/server.crt and server.key, so no TLS listener on 8883:" \
        "the farm's gateway can't connect. infra/readme.md says how to make them." >&2
fi

exec /docker-entrypoint.sh mosquitto -c /mosquitto/staging/mosquitto.conf
