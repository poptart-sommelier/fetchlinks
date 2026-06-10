#!/usr/bin/env bash
# Fetchlinks Pi-side sync cycle.
#
# Runs ON the Pi (ingest role). One cycle, in order:
#   1. Pull control.db down from the VM (feed/subreddit identity + on/off).
#   2. Run ingest (writes posts/health/follows/state into the local data.db).
#   3. Run retention (prune old posts; retention runs ONLY on the Pi).
#   4. Snapshot data.db into a consistent, compacted file (VACUUM INTO).
#   5. Push the snapshot up to the VM, where rsync's temp-file-then-rename
#      lands it atomically (the web reads data.db per-request read-only, so an
#      atomic rename is seamless — no web restart needed).
#
# Transport is Pi-initiated SSH/rsync: no inbound connection to the home
# network and no new service on the VM. The VM restricts the Pi's SSH key to
# rsync against a single directory (see deploy/sync/authorized_keys.example).
#
# control.db is canonical on the VM. The Pi treats its pulled copy as
# read-only and never writes it back.
#
# Configuration (environment variables; sensible defaults below):
#   FETCHLINKS_APP_DIR     checkout dir            (default: parent of deploy/)
#   FETCHLINKS_VENV        python venv dir         (default: $APP_DIR/.venv)
#   FETCHLINKS_CONFIG      ingest TOML config      (default: $APP_DIR/ingest/data/config/fetchlinks.toml)
#   FETCHLINKS_DATA_DB     local Pi-owned data.db  (default: $APP_DIR/ingest/db/data.db)
#   FETCHLINKS_CONTROL_DB  local control.db replica(default: $APP_DIR/ingest/db/control.db)
#   FETCHLINKS_VM_SSH      ssh target user@host    (required, e.g. fetchlinks-sync@vm.example.com)
#   FETCHLINKS_VM_SSH_PORT ssh port                (default: 22)
#   FETCHLINKS_SSH_KEY     ssh identity file       (default: ssh default)
#   FETCHLINKS_REMOTE_CONTROL remote control.db name within the restricted
#                             rsync root            (default: control.db)
#   FETCHLINKS_REMOTE_DATA    remote data.db name within the restricted
#                             rsync root            (default: data.db)
#   FETCHLINKS_RSYNC_TIMEOUT  per-transfer timeout seconds (default: 120)
#
# The ingest config's [paths] must point db -> $FETCHLINKS_DATA_DB and
# control_db -> $FETCHLINKS_CONTROL_DB so ingest reads the pulled control
# replica and writes only the local data.db.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
DEFAULT_APP_DIR="$(cd -- "${SCRIPT_DIR}/../.." && pwd -P)"

APP_DIR="$(realpath -m "${FETCHLINKS_APP_DIR:-${DEFAULT_APP_DIR}}")"
VENV_DIR="${FETCHLINKS_VENV:-${APP_DIR}/.venv}"
CONFIG_FILE="${FETCHLINKS_CONFIG:-${APP_DIR}/ingest/data/config/fetchlinks.toml}"
INGEST_DIR="${APP_DIR}/ingest"
PYTHON_BIN="${VENV_DIR}/bin/python"
# DATA_DB / CONTROL_DB default to the ingest config's [paths] so the snapshot
# target always matches what ingest actually writes/reads (resolved below).
DATA_DB="${FETCHLINKS_DATA_DB:-}"
CONTROL_DB="${FETCHLINKS_CONTROL_DB:-}"

VM_SSH="${FETCHLINKS_VM_SSH:-}"
VM_SSH_PORT="${FETCHLINKS_VM_SSH_PORT:-22}"
SSH_KEY="${FETCHLINKS_SSH_KEY:-}"
REMOTE_CONTROL="${FETCHLINKS_REMOTE_CONTROL:-control.db}"
REMOTE_DATA="${FETCHLINKS_REMOTE_DATA:-data.db}"
RSYNC_TIMEOUT="${FETCHLINKS_RSYNC_TIMEOUT:-120}"

log()  { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\n\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\n\033[1;31m!!\033[0m %s\n' "$*" >&2; exit 1; }

[[ -n "${VM_SSH}" ]]      || die "FETCHLINKS_VM_SSH is required (e.g. fetchlinks-sync@vm.example.com)."
[[ -x "${PYTHON_BIN}" ]]  || die "Python venv not found at ${PYTHON_BIN}."
[[ -f "${CONFIG_FILE}" ]] || die "Ingest config not found at ${CONFIG_FILE}."
command -v rsync   >/dev/null || die "rsync is not installed."
command -v sqlite3 >/dev/null || die "sqlite3 is not installed."

# Resolve db / control_db from the ingest config unless explicitly overridden,
# so the snapshot target always matches what ingest writes and the control
# replica matches what ingest reads.
if [[ -z "${DATA_DB}" || -z "${CONTROL_DB}" ]]; then
    resolved_paths="$(cd "${INGEST_DIR}" && "${PYTHON_BIN}" -c "import config; cfg = config.load_config('${CONFIG_FILE}'); print(cfg.paths.db); print(cfg.paths.control_db)")" \
        || die "Could not resolve [paths] from ${CONFIG_FILE}."
    DATA_DB="${DATA_DB:-$(sed -n 1p <<<"${resolved_paths}")}"
    CONTROL_DB="${CONTROL_DB:-$(sed -n 2p <<<"${resolved_paths}")}"
fi
[[ -n "${DATA_DB}" ]]    || die "Could not determine the data.db path."
[[ -n "${CONTROL_DB}" ]] || die "Could not determine the control.db path."
if [[ "${DATA_DB}" == "${CONTROL_DB}" ]]; then
    die "data.db and control.db resolve to the same file (${DATA_DB}); the two-host split needs distinct [paths].db and [paths].control_db."
fi

# Build the ssh command rsync uses for transport, honouring port + identity.
SSH_OPTS=(-p "${VM_SSH_PORT}" -o BatchMode=yes -o StrictHostKeyChecking=accept-new)
[[ -n "${SSH_KEY}" ]] && SSH_OPTS+=(-i "${SSH_KEY}")
RSH="ssh ${SSH_OPTS[*]}"

RSYNC_BASE=(rsync --timeout="${RSYNC_TIMEOUT}" -e "${RSH}")

# Serialize cycles so a slow run never overlaps the next timer fire.
LOCK_FILE="${FETCHLINKS_LOCK_FILE:-${DATA_DB}.synclock}"
exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
    warn "Another sync cycle is already running (${LOCK_FILE}); exiting."
    exit 0
fi

install -d -m 0755 "$(dirname -- "${DATA_DB}")"
install -d -m 0755 "$(dirname -- "${CONTROL_DB}")"

# ---- 1. pull control.db -----------------------------------------------------
# Non-fatal: if the pull fails we keep ingesting against the existing replica
# (one-cycle lag is acceptable by design). Only a missing first-ever replica
# is fatal.
log "Pulling control.db from ${VM_SSH}:${REMOTE_CONTROL}"
if "${RSYNC_BASE[@]}" "${VM_SSH}:${REMOTE_CONTROL}" "${CONTROL_DB}"; then
    printf '  ok - control.db updated\n'
else
    if [[ -f "${CONTROL_DB}" ]]; then
        warn "control.db pull failed; continuing with existing local replica."
    else
        die "control.db pull failed and no local replica exists at ${CONTROL_DB}."
    fi
fi

# ---- 2. ingest --------------------------------------------------------------
log "Running ingest"
( cd "${INGEST_DIR}" && "${PYTHON_BIN}" "${INGEST_DIR}/fetch_links.py" --config "${CONFIG_FILE}" )

# ---- 3. retention (Pi only) -------------------------------------------------
log "Running retention"
( cd "${INGEST_DIR}" && "${PYTHON_BIN}" "${INGEST_DIR}/retain.py" --config "${CONFIG_FILE}" )

# ---- 4. snapshot ------------------------------------------------------------
# VACUUM INTO produces a consistent, compacted copy without touching the live
# DB. Write it beside data.db so the push reads a stable file.
SNAPSHOT="${DATA_DB}.snapshot"
log "Snapshotting data.db -> ${SNAPSHOT}"
rm -f "${SNAPSHOT}"
sqlite3 "${DATA_DB}" "VACUUM INTO '${SNAPSHOT}'"

# ---- 5. push ----------------------------------------------------------------
# rsync transfers to a hidden temp file in the destination directory and
# renames it into place on completion — atomic on the VM's filesystem.
log "Pushing snapshot to ${VM_SSH}:${REMOTE_DATA}"
"${RSYNC_BASE[@]}" "${SNAPSHOT}" "${VM_SSH}:${REMOTE_DATA}"
rm -f "${SNAPSHOT}"

log "Sync cycle complete."
