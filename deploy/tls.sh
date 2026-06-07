#!/usr/bin/env bash
# Fetchlinks TLS / nginx provisioner.
#
# Installs nginx, drops in the fetchlinks-web reverse-proxy site for the
# given domain, and obtains a Let's Encrypt certificate via certbot.
#
# Decoupled from bootstrap.sh so you can:
#   * stand the box up first and add TLS later, or
#   * re-run only the TLS step when changing domain / rotating cert.
#
# Run on the VM, as root, AFTER bootstrap.sh has succeeded at least once.
#
# Usage:
#   sudo FETCHLINKS_DOMAIN=fetchlinks.example.com \
#        FETCHLINKS_EMAIL=you@example.com \
#        ~/fetchlinks/deploy/tls.sh
#
# Or positionally:
#   sudo ~/fetchlinks/deploy/tls.sh fetchlinks.example.com you@example.com
#
# Re-running is safe; certbot will renew rather than re-issue when the cert
# is still valid.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
DEFAULT_APP_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
APP_DIR="$(realpath -m "${FETCHLINKS_APP_DIR:-${DEFAULT_APP_DIR}}")"
SITE_TEMPLATE="${APP_DIR}/deploy/nginx/fetchlinks-web.conf.example"
SITE_AVAILABLE="/etc/nginx/sites-available/fetchlinks-web.conf"
SITE_ENABLED="/etc/nginx/sites-enabled/fetchlinks-web.conf"

DOMAIN="${FETCHLINKS_DOMAIN:-${1:-}}"
EMAIL="${FETCHLINKS_EMAIL:-${2:-}}"

log()  { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\n\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\n\033[1;31m!!\033[0m %s\n' "$*" >&2; exit 1; }

if [[ $EUID -ne 0 ]]; then
    die "Run as root (sudo $0)."
fi

if [[ -z "${DOMAIN}" ]]; then
    die "FETCHLINKS_DOMAIN is required (env var or first positional arg)."
fi
if [[ -z "${EMAIL}" ]]; then
    die "FETCHLINKS_EMAIL is required (env var or second positional arg)."
fi

if [[ ! -f "${SITE_TEMPLATE}" ]]; then
    die "Missing nginx site template at ${SITE_TEMPLATE}. Run bootstrap.sh first."
fi

if ! systemctl is-active --quiet fetchlinks-web.service; then
    warn "fetchlinks-web.service is not active; nginx will proxy to a down upstream."
fi

# ---- packages ---------------------------------------------------------------

log "Installing nginx and certbot"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y nginx python3-certbot-nginx

# ---- nginx site -------------------------------------------------------------

log "Rendering nginx site for ${DOMAIN}"
sed "s/fetchlinks.example.com/${DOMAIN}/g" "${SITE_TEMPLATE}" > "${SITE_AVAILABLE}"
ln -sf "${SITE_AVAILABLE}" "${SITE_ENABLED}"
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl enable --now nginx
systemctl reload nginx

# ---- certbot ----------------------------------------------------------------

log "Requesting / renewing certificate for ${DOMAIN}"
certbot --nginx --non-interactive --agree-tos \
    -m "${EMAIL}" -d "${DOMAIN}" --redirect

# certbot installs its own systemd timer (certbot.timer) on Ubuntu; make sure
# it's enabled so renewals happen unattended.
if systemctl list-unit-files certbot.timer >/dev/null 2>&1; then
    systemctl enable --now certbot.timer
fi

log "Done. nginx + TLS configured for https://${DOMAIN}"
