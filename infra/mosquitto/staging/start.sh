#!/bin/sh
# Builds the broker's password file from the MQTT_PASSWORD_* variables, then starts Mosquitto.
# MQTT_PASSWORD_ZC1 becomes the account ZC1, MQTT_PASSWORD_CLOUD_API the account CLOUD-api.
set -eu

passwords=/tmp/mosquitto-passwords
: > "$passwords"
chmod 600 "$passwords"

for variable in $(env | sed -n 's/^\(MQTT_PASSWORD_[A-Z0-9_]*\)=.*/\1/p'); do
    account=${variable#MQTT_PASSWORD_}
    case "$account" in
        CLOUD_*) account="CLOUD-$(echo "${account#CLOUD_}" | tr '[:upper:]' '[:lower:]')" ;;
    esac
    eval "password=\$$variable"
    mosquitto_passwd -b "$passwords" "$account" "$password"
done

chown mosquitto:mosquitto "$passwords"
exec /docker-entrypoint.sh mosquitto -c /mosquitto/staging/mosquitto.conf
