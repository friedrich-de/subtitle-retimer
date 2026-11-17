#!/usr/bin/env bash
# Prepares a server for the Subtitle Retimer service. Run once as root, from a
# directory that holds this script, retimer.service, retimer.env and
# retimer_secrets.py. Running it again is safe: it only puts back what is missing
# and refreshes the unit and the secrets tool.
#
# It makes:
#   retimer            the account the service runs as (no login, no home)
#   /opt/retimer       code and its Python, owned by the deploying account
#   /var/lib/retimer   jobs, uploads and the cache, owned by the service
#   /etc/retimer       settings, and later the keys
#   retimer.service    enabled, so it starts at boot once code and keys exist
#
# It removes nothing and touches no other service.
set -euo pipefail

SERVICE_USER=retimer
DEPLOY_USER=${DEPLOY_USER:-deploy}

if [ "$(id -u)" -ne 0 ]; then
    echo "error: run this as root" >&2
    exit 1
fi
if ! id -u "$DEPLOY_USER" >/dev/null 2>&1; then
    echo "error: there is no account $DEPLOY_USER. Set DEPLOY_USER to the one that deploys." >&2
    exit 1
fi
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
for file in retimer.service retimer.env retimer_secrets.py; do
    if [ ! -f "$here/$file" ]; then
        echo "error: $file is not next to this script" >&2
        exit 1
    fi
done

if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --user-group --home-dir /var/lib/retimer --no-create-home \
        --shell /usr/sbin/nologin "$SERVICE_USER"
    echo "created the account $SERVICE_USER"
fi

# The deploying account writes the code. The service may read it and nobody else.
install -d -o "$DEPLOY_USER" -g "$SERVICE_USER" -m 0750 /opt/retimer
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0700 /var/lib/retimer
install -d -o root -g root -m 0755 /etc/retimer
if [ ! -e /etc/retimer/retimer.env ]; then
    install -o root -g root -m 0644 "$here/retimer.env" /etc/retimer/retimer.env
fi
install -o root -g root -m 0644 "$here/retimer.service" /etc/systemd/system/retimer.service
install -o root -g root -m 0755 "$here/retimer_secrets.py" /usr/local/sbin/retimer-secrets

systemctl daemon-reload
systemctl enable retimer.service >/dev/null 2>&1
echo "the server is prepared: retimer.service is enabled and starts once code and keys are there"
