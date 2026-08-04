#!/usr/bin/env bash
#
# Fetchlinks Raspberry Pi installer.
#
# Installs the Collector and the Publisher on a Debian-family machine and
# leaves the whole deployment inside this checkout:
#
#   ~/fetchlinks/                 this checkout
#   ~/fetchlinks/.venv/           Python environment
#   ~/fetchlinks/runtime/         everything mutable, gitignored
#     config/                     fetchlinks.toml + source credentials (0600)
#     catalog/                    catalog snapshot pulled from PostgreSQL
#     state/                      collector resume state
#     outbox/                     batch spool
#     logs/                       collector log
#     publisher.env               Neon URL, publisher role only (0600)
#
# Run it as your normal login user, not with sudo. It escalates only for the
# two things that genuinely need root: apt and systemd.
#
#   ./deploy/bootstrap.sh
#
# Idempotent: re-run it after `git pull` to reinstall dependencies and refresh
# the units. It never overwrites anything under runtime/.
#
# There is no web server here. The web GUI runs on Vercel and the database on
# Neon; this host only collects and publishes.

set -euo pipefail

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# --- context ---------------------------------------------------------------

[[ ${EUID} -ne 0 ]] || die "run this as your normal user, not root or sudo. It calls sudo itself where needed."

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_USER="$(id -un)"
RUNTIME_DIR="${APP_DIR}/runtime"
VENV_DIR="${APP_DIR}/.venv"
PYTHON_BIN="${VENV_DIR}/bin/python"

[[ -d ${APP_DIR}/ingest ]] || die "no ingest/ directory under ${APP_DIR}; is this a Fetchlinks checkout?"
command -v systemctl >/dev/null || die "systemd is required"
command -v sudo >/dev/null || die "sudo is required"

log "Installing Fetchlinks in ${APP_DIR} for user ${APP_USER}"

# The unit files reference the venv and runtime by absolute path, and systemd
# resolves nothing for us. Refuse a path systemd cannot express rather than
# installing units that fail at first trigger with a confusing message.
case ${APP_DIR} in
  *[[:space:]]*) die "the checkout path contains whitespace, which systemd unit paths cannot express: ${APP_DIR}" ;;
esac

# --- packages --------------------------------------------------------------

DPKG_ARCH="$(dpkg --print-architecture 2>/dev/null || echo unknown)"

log "Installing system packages (dpkg architecture: ${DPKG_ARCH})"
sudo apt-get update -qq
sudo apt-get install -y -qq \
  ca-certificates \
  git \
  python3 \
  python3-venv \
  python3-dev

# Raspberry Pi OS ships a 64-bit kernel with a 32-bit userland, so `uname -m`
# says aarch64 while dpkg says armhf and Python's wheel tags say armv8l.
# psycopg-binary publishes no 32-bit ARM wheels, so `psycopg[binary]` cannot
# resolve at all there. The fix is the pure-Python implementation against the
# system libpq.
#
# This cannot be expressed as a PEP 508 marker in requirements.txt:
# `platform_machine` comes from uname and therefore reports aarch64 on this
# machine, which would select exactly the wheel that does not exist. Hence the
# explicit branch here.
NEEDS_SYSTEM_LIBPQ=0
case ${DPKG_ARCH} in
  armhf|armel|i386)
    NEEDS_SYSTEM_LIBPQ=1
    log "32-bit userland detected; using pure-Python psycopg with the system libpq"
    sudo apt-get install -y -qq libpq5
    ;;
esac

# --- python environment ----------------------------------------------------

if [[ ! -x ${PYTHON_BIN} ]]; then
  log "Creating the virtual environment"
  python3 -m venv "${VENV_DIR}"
else
  log "Reusing the existing virtual environment"
fi

log "Installing Python dependencies"
"${PYTHON_BIN}" -m pip install --quiet --upgrade pip

REQUIREMENTS="${APP_DIR}/ingest/requirements.txt"
SCRATCH="$(mktemp -d)"
trap 'rm -rf "${SCRATCH}"' EXIT

if [[ ${NEEDS_SYSTEM_LIBPQ} -eq 1 ]]; then
  # Swap the binary extra for the plain package. Everything else is untouched,
  # so the deployed dependency set stays in step with the checked-in one.
  sed -e 's/^psycopg\[binary\]$/psycopg/' "${REQUIREMENTS}" > "${SCRATCH}/requirements.txt"
  REQUIREMENTS="${SCRATCH}/requirements.txt"
fi

"${PYTHON_BIN}" -m pip install --quiet --upgrade -r "${REQUIREMENTS}"

# psycopg fails at import, not at connect, when it can find no libpq. Catching
# that here turns a confusing hourly unit failure into an install-time error.
if ! "${PYTHON_BIN}" -c 'import psycopg' 2>/dev/null; then
  die "psycopg imported no working libpq implementation. On a 32-bit userland, install libpq5."
fi

# --- runtime layout --------------------------------------------------------

log "Preparing ${RUNTIME_DIR}"
mkdir -p "${RUNTIME_DIR}"/{config,catalog,state,outbox,logs}
chmod 700 "${RUNTIME_DIR}/config"

install_once() {
  # Copy a template into place exactly once. Local edits always win, which is
  # what makes re-running this script after a git pull safe.
  local src=$1 dest=$2 mode=$3
  if [[ -e ${dest} ]]; then
    printf '    kept   %s\n' "${dest#"${APP_DIR}/"}"
    return
  fi
  install -m "${mode}" "${src}" "${dest}"
  printf '    wrote  %s\n' "${dest#"${APP_DIR}/"}"
}

install_once "${APP_DIR}/deploy/fetchlinks.pi.toml"  "${RUNTIME_DIR}/config/fetchlinks.toml" 0600
install_once "${APP_DIR}/deploy/publisher.env.example" "${RUNTIME_DIR}/publisher.env"        0600

# Seed lists are only consulted by `publish_tool.py bootstrap-catalog` against
# an empty database. Copied so a rebuild can seed from scratch without the
# checkout being the source of truth afterwards.
for seed in rss_feeds.txt subreddits.txt; do
  if [[ -f ${APP_DIR}/ingest/data/config/${seed} ]]; then
    install_once "${APP_DIR}/ingest/data/config/${seed}" "${RUNTIME_DIR}/config/${seed}" 0600
  fi
done

# Credentials may have been dropped in by hand; make sure none of them are
# world-readable regardless of how they arrived.
shopt -s nullglob
for cred in "${RUNTIME_DIR}"/config/*.json; do
  chmod 600 "${cred}"
done
shopt -u nullglob

# --- systemd units ---------------------------------------------------------

log "Installing systemd units"
UNIT_SRC="${APP_DIR}/deploy/systemd"
UNITS=(
  fetchlinks-collect.service fetchlinks-collect.timer
  fetchlinks-publish.service fetchlinks-publish.timer
  fetchlinks-retain.service  fetchlinks-retain.timer
)

render_dir="${SCRATCH}/units"
mkdir -p "${render_dir}"

for unit in "${UNITS[@]}"; do
  sed \
    -e "s|__FETCHLINKS_APP_DIR__|${APP_DIR}|g" \
    -e "s|__FETCHLINKS_RUNTIME_DIR__|${RUNTIME_DIR}|g" \
    -e "s|__FETCHLINKS_USER__|${APP_USER}|g" \
    "${UNIT_SRC}/${unit}" > "${render_dir}/${unit}"
  # A leftover placeholder means a unit gained a token this script does not
  # know about. Fail here rather than at 03:12 on a Sunday.
  if grep -q '__FETCHLINKS_' "${render_dir}/${unit}"; then
    die "unsubstituted placeholder in ${unit}"
  fi
done

sudo install -m 0644 -t /etc/systemd/system "${render_dir}"/*.service "${render_dir}"/*.timer
sudo systemctl daemon-reload

# Remove units from the retired single-host and two-host SQLite topologies, so
# an upgraded machine does not keep an old timer running against a database
# that no longer exists.
for stale in fetchlinks-web.service fetchlinks-ingest.service fetchlinks-ingest.timer \
             fetchlinks-sync.service fetchlinks-sync.timer \
             fetchlinks-export-rss-feeds.service fetchlinks-export-rss-feeds.timer; do
  if [[ -e /etc/systemd/system/${stale} ]]; then
    log "Removing retired unit ${stale}"
    sudo systemctl disable --now "${stale}" >/dev/null 2>&1 || true
    sudo rm -f "/etc/systemd/system/${stale}"
  fi
done
sudo systemctl daemon-reload

# --- timers ----------------------------------------------------------------

# The collector is safe to enable unconditionally: it needs no database and no
# credential beyond whatever sources are configured. The publisher is not, so
# it stays disabled until publisher.env holds a real URL. Enabling it with the
# placeholder would produce an hourly failing unit and nothing else.
log "Enabling the collector timer"
sudo systemctl enable --now fetchlinks-collect.timer

if grep -q 'PASSWORD@ep-xxxx-pooler' "${RUNTIME_DIR}/publisher.env"; then
  warn "runtime/publisher.env still holds the placeholder URL."
  warn "The publisher and retention timers were left disabled. Edit it, then run:"
  warn "  sudo systemctl enable --now fetchlinks-publish.timer fetchlinks-retain.timer"
else
  log "Enabling the publisher and retention timers"
  sudo systemctl enable --now fetchlinks-publish.timer fetchlinks-retain.timer
fi

# --- report ----------------------------------------------------------------

log "Installed. Current state:"
systemctl list-timers --all 'fetchlinks-*' --no-pager || true

cat <<EOF

Next steps
  1. Put source credentials in ${RUNTIME_DIR}/config/ as reddit.json,
     bluesky.json and mastodon-infosec.json (see ingest/SETUP.md).
  2. Put the publisher role's Neon URL in ${RUNTIME_DIR}/publisher.env.
  3. Pull the catalog and run one cycle by hand before trusting the timers:
       systemctl start fetchlinks-publish.service   # syncs the catalog
       systemctl start fetchlinks-collect.service
       ${PYTHON_BIN} ${APP_DIR}/ingest/publish_tool.py \\
         --config ${RUNTIME_DIR}/config/fetchlinks.toml status

Logs
  journalctl -u fetchlinks-collect.service -n 50
  journalctl -u fetchlinks-publish.service -n 50
EOF
