#!/usr/bin/env bash
# Fetchlinks one-shot VM bootstrap / updater.
#
# Run this ON the VM, as root, from a fresh Ubuntu 24.04 install:
#
#   sudo apt-get update && sudo apt-get install -y git
#   sudo git clone https://github.com/poptart-sommelier/fetchlinks.git /opt/fetchlinks
#   sudo /opt/fetchlinks/deploy/bootstrap.sh
#
# Re-running this script later is safe; it acts as the upgrade path
# (fast-forwards git, rebuilds web, restarts services).
#
# Public TLS / nginx is provisioned by a separate script, deploy/tls.sh.
# Run it once you have a DNS record pointing at this VM.
#
# Optional environment variables:
#   FETCHLINKS_REPO_URL git URL to clone/pull (default: poptart-sommelier/fetchlinks)
#   FETCHLINKS_REPO_REF branch/tag to deploy   (default: master)

set -euo pipefail

# ---- config -----------------------------------------------------------------

APP_USER="fetchlinks"
APP_GROUP="fetchlinks"
APP_DIR="/opt/fetchlinks"
VENV_DIR="${APP_DIR}/.venv"
INGEST_DIR="${APP_DIR}/ingest"
WEB_DIR="${APP_DIR}/web"
CONFIG_FILE="${INGEST_DIR}/data/config/fetchlinks.toml"
RSS_FEEDS_FILE="${INGEST_DIR}/data/config/rss_feeds.txt"
WEB_ENV_FILE="${WEB_DIR}/.env.production"
NODE_MAJOR=24
PYTHON_BIN="/usr/bin/python3.12"

REPO_URL="${FETCHLINKS_REPO_URL:-https://github.com/poptart-sommelier/fetchlinks.git}"
REPO_REF="${FETCHLINKS_REPO_REF:-master}"

log()  { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\n\033[1;33m!!\033[0m %s\n' "$*" >&2; }

# ---- sanity -----------------------------------------------------------------

if [[ $EUID -ne 0 ]]; then
    echo "Run as root (sudo $0)." >&2
    exit 1
fi

if ! grep -q '^ID=ubuntu' /etc/os-release; then
    warn "Not Ubuntu; tested only on Ubuntu 24.04. Continuing anyway."
fi

# ---- apt + base packages ----------------------------------------------------

log "Updating apt and installing base packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y \
    git curl ca-certificates sqlite3 ufw unattended-upgrades \
    python3.12 python3.12-venv python3.12-dev \
    build-essential libffi-dev libssl-dev

# NodeSource for current Node major
if ! command -v node >/dev/null || [[ "$(node -v 2>/dev/null)" != v${NODE_MAJOR}.* ]]; then
    log "Installing Node.js ${NODE_MAJOR}.x from NodeSource"
    curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash -
    apt-get install -y nodejs
fi

# ---- unattended-upgrades ----------------------------------------------------

log "Enabling unattended-upgrades schedule"
cat >/etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF

# ---- user, group, directories ----------------------------------------------

log "Ensuring ${APP_USER} user and app directory"
getent group  "${APP_GROUP}" >/dev/null || groupadd --system "${APP_GROUP}"
getent passwd "${APP_USER}"  >/dev/null || useradd  --system --gid "${APP_GROUP}" \
    --home-dir "${APP_DIR}" --shell /usr/sbin/nologin "${APP_USER}"

install -d -o "${APP_USER}" -g "${APP_GROUP}" -m 0755 "${APP_DIR}"
chown -R "${APP_USER}:${APP_GROUP}" "${APP_DIR}"

# ---- firewall ---------------------------------------------------------------

log "Configuring ufw"
ufw --force default deny incoming  >/dev/null
ufw --force default allow outgoing >/dev/null
for port in 22 80 443; do ufw allow "${port}/tcp" >/dev/null; done
ufw --force enable >/dev/null

# ---- repo -------------------------------------------------------------------

log "Syncing repository (${REPO_URL} @ ${REPO_REF})"
if [[ -d "${APP_DIR}/.git" ]]; then
    sudo -u "${APP_USER}" git -C "${APP_DIR}" fetch origin "${REPO_REF}"
    sudo -u "${APP_USER}" git -C "${APP_DIR}" merge --ff-only "origin/${REPO_REF}"
else
    # The README's bootstrap clones into ${APP_DIR} as root; reset ownership
    # in case this is the first run after that clone.
    if [[ -d "${APP_DIR}" ]] && [[ -z "$(ls -A "${APP_DIR}")" ]]; then
        sudo -u "${APP_USER}" git clone --branch "${REPO_REF}" "${REPO_URL}" "${APP_DIR}"
    else
        echo "${APP_DIR} exists but is not a git checkout. Move it aside or clone the repo there first." >&2
        exit 1
    fi
fi

chown -R "${APP_USER}:${APP_GROUP}" "${APP_DIR}"
install -d -o "${APP_USER}" -g "${APP_GROUP}" -m 0755 "${INGEST_DIR}/db"
install -d -o "${APP_USER}" -g "${APP_GROUP}" -m 0755 "${INGEST_DIR}/data/logs"
install -d -o "${APP_USER}" -g "${APP_GROUP}" -m 0755 "${INGEST_DIR}/data/config"

# ---- python venv + ingest deps ---------------------------------------------

log "Building Python venv and installing ingest requirements"
if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    sudo -u "${APP_USER}" "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi
sudo -u "${APP_USER}" "${VENV_DIR}/bin/pip" install --upgrade pip >/dev/null
sudo -u "${APP_USER}" "${VENV_DIR}/bin/pip" install -r "${INGEST_DIR}/requirements.txt"

# ---- web build --------------------------------------------------------------

log "Building Next.js web app"
sudo -u "${APP_USER}" bash -c "cd '${WEB_DIR}' && npm ci && npm run build"

# ---- config + env files -----------------------------------------------------

if [[ ! -f "${WEB_ENV_FILE}" ]]; then
    log "Seeding ${WEB_ENV_FILE} from example"
    install -o "${APP_USER}" -g "${APP_GROUP}" -m 0600 \
        "${WEB_DIR}/.env.production.example" "${WEB_ENV_FILE}"
fi

# ---- systemd units ----------------------------------------------------------

log "Installing systemd units"
install -m 0644 "${APP_DIR}/deploy/systemd/fetchlinks-web.service"                 /etc/systemd/system/
install -m 0644 "${APP_DIR}/deploy/systemd/fetchlinks-ingest.service"              /etc/systemd/system/
install -m 0644 "${APP_DIR}/deploy/systemd/fetchlinks-ingest.timer"                /etc/systemd/system/
install -m 0644 "${APP_DIR}/deploy/systemd/fetchlinks-retain.service"              /etc/systemd/system/
install -m 0644 "${APP_DIR}/deploy/systemd/fetchlinks-retain.timer"                /etc/systemd/system/
install -m 0644 "${APP_DIR}/deploy/systemd/fetchlinks-export-rss-feeds.service"    /etc/systemd/system/
install -m 0644 "${APP_DIR}/deploy/systemd/fetchlinks-export-rss-feeds.timer"      /etc/systemd/system/
systemctl daemon-reload

systemctl enable --now fetchlinks-web.service
systemctl enable --now fetchlinks-ingest.timer
systemctl enable --now fetchlinks-retain.timer
systemctl enable --now fetchlinks-export-rss-feeds.timer
systemctl restart   fetchlinks-export-rss-feeds.timer
systemctl restart   fetchlinks-web.service

# ---- one-time seed: import ingest/data/config/rss_feeds.txt into the DB if the
# rss_feeds table is empty. No-op on upgrade once the operator has feeds.
log "Seeding rss_feeds table from ${RSS_FEEDS_FILE} if empty"
sudo -u "${APP_USER}" "${VENV_DIR}/bin/python" \
    "${INGEST_DIR}/rss_feed_import.py" \
    --config "${CONFIG_FILE}" \
    --seed-if-empty "${RSS_FEEDS_FILE}" || \
    warn "rss_feeds seed step reported a failure; check the output above."

log "Exporting rss_feeds table back to ${RSS_FEEDS_FILE}"
systemctl start fetchlinks-export-rss-feeds.service || \
    warn "rss_feeds export step reported a failure; check the journal."

# nginx + TLS are provisioned separately by deploy/tls.sh.

# ---- final summary ----------------------------------------------------------

log "Done. Status:"
systemctl --no-pager --lines=0 status fetchlinks-web.service                  || true
systemctl --no-pager --lines=0 status fetchlinks-ingest.timer                 || true
systemctl --no-pager --lines=0 status fetchlinks-retain.timer                 || true
systemctl --no-pager --lines=0 status fetchlinks-export-rss-feeds.timer       || true

cat <<EOF

------------------------------------------------------------
Manual steps still required:
  1. Ensure credential files referenced by ${CONFIG_FILE} exist.
      bootstrap.sh does not create, copy, or chmod credentials.
  2. Edit ${WEB_ENV_FILE} and set FETCHLINKS_ADMIN_USER / FETCHLINKS_ADMIN_PASS,
      then restart fetchlinks-web.service.
  3. (Optional) edit ${RSS_FEEDS_FILE} before re-running this
      script if you want a different first-bootstrap seed. The DB is the
      live source of truth after seeding; later use the web admin or
      rss_feed_import.py to update the DB. fetchlinks-export-rss-feeds.timer
      writes the DB snapshot back to this file every 5 minutes.
  4. (Optional) drop an existing DB snapshot at:
         ${INGEST_DIR}/db/fetchlinks.db
  5. Trigger an ingest run to verify:
       sudo systemctl start fetchlinks-ingest.service
       sudo journalctl -u fetchlinks-ingest.service -n 50 --no-pager
  6. (Optional) provision nginx + TLS once DNS points at this VM:
       sudo FETCHLINKS_DOMAIN=fetchlinks.example.com \
            FETCHLINKS_EMAIL=you@example.com \
            ${APP_DIR}/deploy/tls.sh
------------------------------------------------------------
EOF
