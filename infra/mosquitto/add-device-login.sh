#!/bin/sh
# Adds or changes one farm device's login on the staging broker (T1-36, contract 3).
#
# Run from the repo root on the machine that runs the staging stack (Git Bash on Windows):
#   sh infra/mosquitto/add-device-login.sh GW1
#
# It makes a random password, keeps only its hash in infra/mosquitto/staging/secrets/passwords
# (git ignores it) and prints the password once: put it in the password manager, then in the
# device. Nothing else stores it. The device must have a block in the site's ACL file
# (MQTT_ACL, staging/acl.conf by default), or be the prototype rig's gateway (RIG_GATEWAY_LOGIN,
# gateway-01 by default), whose block start.sh writes:
#   MQTT_ACL=acl-coimbatore-ph1.conf sh infra/mosquitto/add-device-login.sh gateway-01
# A login here replaces the device's MQTT_PASSWORD_<ID> from infra/.env.staging. CLOUD-* accounts
# stay in infra/.env.staging.
#
# The broker reads its logins when it starts, so restart it afterwards:
#   docker compose --env-file infra/.env.staging -f infra/docker-compose.staging.yml restart mqtt
set -eu

MOSQUITTO_IMAGE=eclipse-mosquitto:2.0.22  # the broker's own image, for its mosquitto_passwd

here=$(cd "$(dirname "$0")" && pwd)
acl="$here/staging/${MQTT_ACL:-acl.conf}"
rig_gateway=${RIG_GATEWAY_LOGIN:-gateway-01}
secrets="$here/staging/secrets"

device=${1:-}
case "$device" in
    '' | -*)
        echo "usage: sh infra/mosquitto/add-device-login.sh DEVICE_ID   (for example GW1)" >&2
        exit 2 ;;
    CLOUD-*)
        echo "add-device-login: $device is a back-end account; its password is in infra/.env.staging" >&2
        exit 2 ;;
    *[!A-Za-z0-9-]*)
        echo "add-device-login: '$device' is not a device ID (contract 2.2: GW1, ZC1 ...)" >&2
        exit 2 ;;
esac
if [ "$device" != "$rig_gateway" ] && ! tr -d '\r' < "$acl" | grep -qx "user $device"; then
    echo "add-device-login: $device has no block in $acl, so the broker" \
        "would give it no topics. Add its permissions there first." >&2
    exit 1
fi

mkdir -p "$secrets"
touch "$secrets/passwords"
chmod 600 "$secrets/passwords" 2>/dev/null || true

# 24 random bytes as hex: a 48-character password.
password=$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')

# The folder as Docker wants it: C:/... from Git Bash on Windows, the plain path elsewhere.
mount=$(cd "$secrets" && { pwd -W 2>/dev/null || pwd; })
# The password reaches the container in its environment, never on a command line.
NEW_DEVICE_PASSWORD=$password MSYS_NO_PATHCONV=1 docker run --rm \
    --user "$(id -u):$(id -g)" \
    -e NEW_DEVICE_PASSWORD \
    -v "$mount:/secrets" \
    "$MOSQUITTO_IMAGE" \
    sh -c 'mosquitto_passwd -b /secrets/passwords "$1" "$NEW_DEVICE_PASSWORD"' sh "$device"

echo "Login for $device saved, as a hash only, in infra/mosquitto/staging/secrets/passwords."
echo
echo "Password for $device. Put it in the password manager now; it is not stored anywhere else:"
echo
echo "    $password"
echo
echo "Then restart the broker so it reads the login:"
echo "    docker compose --env-file infra/.env.staging -f infra/docker-compose.staging.yml restart mqtt"
