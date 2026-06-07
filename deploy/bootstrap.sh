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
SUBREDDITS_FILE="${INGEST_DIR}/data/config/subreddits.txt"
WEB_ENV_FILE="${WEB_DIR}/.env.production"
NODE_MAJOR=24
PYTHON_BIN="/usr/bin/python3.12"

REPO_URL="${FETCHLINKS_REPO_URL:-https://github.com/poptart-sommelier/fetchlinks.git}"
REPO_REF="${FETCHLINKS_REPO_REF:-master}"

log()  { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\n\033[1;33m!!\033[0m %s\n' "$*" >&2; }

GENERATED_ADMIN_PASSWORD=""
declare -a SKIPPED_CREDENTIAL_SOURCES=()
declare -a VALIDATION_FAILED_SOURCES=()

prompt_yes_no() {
    local prompt="$1"
    local default_answer="${2:-y}"
    local answer=""
    local suffix="[Y/n]"

    if [[ "${default_answer}" == "n" ]]; then
        suffix="[y/N]"
    fi

    while true; do
        read -r -p "${prompt} ${suffix} " answer
        answer="${answer:-${default_answer}}"
        case "${answer,,}" in
            y|yes) return 0 ;;
            n|no)  return 1 ;;
            *)     printf 'Please answer yes or no.\n' ;;
        esac
    done
}

json_object_is_valid() {
    "${PYTHON_BIN}" -c 'import json, sys; value = json.loads(sys.stdin.read()); sys.exit(0 if isinstance(value, dict) else 1)' <<<"$1" >/dev/null 2>&1
}

read_json_or_path_input() {
    local prompt="$1"
    local value=""
    local next_line=""
    local trimmed=""

    printf '%s' "${prompt}"
    IFS= read -r -s value || true
    printf '\n'

    trimmed="${value#"${value%%[![:space:]]*}"}"
    if [[ "${trimmed}" == \{* ]]; then
        until json_object_is_valid "${value}"; do
            printf 'json> '
            IFS= read -r -s next_line || break
            printf '\n'
            value+=$'\n'"${next_line}"
        done
    fi

    printf '%s' "${value}"
}

resolve_existing_input_path() {
    local input_path="$1"
    "${PYTHON_BIN}" - "${input_path}" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1]).expanduser()
if path.is_file():
    print(path)
    raise SystemExit(0)
raise SystemExit(1)
PY
}

install_credential_input() {
    local label="$1"
    local target_path="$2"
    local input_value="$3"
    local source_path=""
    local target_dir=""
    local temp_json=""
    local trimmed="${input_value#"${input_value%%[![:space:]]*}"}"

    if [[ -z "${input_value//[[:space:]]/}" ]]; then
        warn "Skipped ${label}; no credential input was provided."
        SKIPPED_CREDENTIAL_SOURCES+=("${label}")
        return 1
    fi

    target_dir="$(dirname "${target_path}")"
    install -d -o "${APP_USER}" -g "${APP_GROUP}" -m 0700 "${target_dir}"

    if [[ "${trimmed}" != \{* ]] && source_path="$(resolve_existing_input_path "${input_value}" 2>/dev/null)"; then
        install -o "${APP_USER}" -g "${APP_GROUP}" -m 0600 "${source_path}" "${target_path}"
        return 0
    fi

    temp_json="$(mktemp)"
    printf '%s\n' "${input_value}" >"${temp_json}"
    if ! "${PYTHON_BIN}" - "${temp_json}" "${target_path}" <<'PY'
from pathlib import Path
import json
import sys

input_path = Path(sys.argv[1])
target_path = Path(sys.argv[2])

try:
    value = json.loads(input_path.read_text(encoding='utf-8'))
except json.JSONDecodeError as exc:
    print(f'Invalid JSON: {exc}', file=sys.stderr)
    raise SystemExit(1)

if not isinstance(value, dict):
    print('Credential JSON must be an object.', file=sys.stderr)
    raise SystemExit(1)

target_path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n', encoding='utf-8')
PY
    then
        rm -f "${temp_json}"
        warn "Skipped ${label}; input was neither a readable path nor a valid JSON object."
        SKIPPED_CREDENTIAL_SOURCES+=("${label}")
        return 1
    fi

    rm -f "${temp_json}"
    chown "${APP_USER}:${APP_GROUP}" "${target_path}"
    chmod 0600 "${target_path}"
}

list_enabled_credential_targets() {
    APP_HOME="${APP_DIR}" "${PYTHON_BIN}" - "${CONFIG_FILE}" <<'PY'
from pathlib import Path
import os
import sys
import tomllib

config_path = Path(sys.argv[1])
base = config_path.resolve().parent
app_home = Path(os.environ['APP_HOME'])

def as_bool(value, default):
    return bool(default if value is None else value)

def resolve_path(value):
    if not isinstance(value, str) or not value.strip():
        return None
    if value == '~':
        path = app_home
    elif value.startswith('~/'):
        path = app_home / value[2:]
    else:
        path = Path(value).expanduser()
    if not path.is_absolute():
        path = (base / path).resolve()
    return path

with config_path.open('rb') as handle:
    raw = tomllib.load(handle)

sources = raw.get('sources', {})

reddit = sources.get('reddit') or {}
if as_bool(reddit.get('enabled'), True):
    path = resolve_path(reddit.get('credential_location'))
    if path:
        print(f'Reddit|{path}')

bluesky = sources.get('bluesky') or {}
if as_bool(bluesky.get('enabled'), False):
    path = resolve_path(bluesky.get('credential_location'))
    if path:
        print(f'Bluesky|{path}')

mastodon = sources.get('mastodon') or {}
if as_bool(mastodon.get('enabled'), False):
    for instance in mastodon.get('instances') or []:
        if not as_bool(instance.get('enabled'), True):
            continue
        name = instance.get('name') or 'default'
        path = resolve_path(instance.get('credential_location'))
        if path:
            print(f'Mastodon {name}|{path}')
PY
}

configure_missing_credentials() {
    local source_label=""
    local credential_path=""
    local credential_input=""
    local credential_entry=""
    local missing_entries=()

    log "Checking ingest credentials"
    while IFS='|' read -r source_label credential_path; do
        [[ -z "${source_label}" ]] && continue
        if [[ -f "${credential_path}" ]]; then
            printf '  ok - %s credentials exist at %s\n' "${source_label}" "${credential_path}"
            continue
        fi

        warn "Missing ${source_label} credentials at ${credential_path}."
        missing_entries+=("${source_label}|${credential_path}")
    done < <(list_enabled_credential_targets)

    if [[ "${#missing_entries[@]}" -eq 0 ]]; then
        return 0
    fi

    if ! prompt_yes_no "Configure missing ingest credentials now?" "y"; then
        for credential_entry in "${missing_entries[@]}"; do
            IFS='|' read -r source_label credential_path <<<"${credential_entry}"
            SKIPPED_CREDENTIAL_SOURCES+=("${source_label}")
        done
        return 0
    fi

    for credential_entry in "${missing_entries[@]}"; do
        IFS='|' read -r source_label credential_path <<<"${credential_entry}"
        if prompt_yes_no "Configure ${source_label} credentials now?" "y"; then
            credential_input="$(read_json_or_path_input "${source_label} JSON or path [${credential_path}] (input hidden, blank skips): ")"
            install_credential_input "${source_label}" "${credential_path}" "${credential_input}" || true
        else
            SKIPPED_CREDENTIAL_SOURCES+=("${source_label}")
        fi
    done
}

generate_admin_password() {
    "${PYTHON_BIN}" -c 'import secrets; print(secrets.token_urlsafe(24))'
}

set_web_env_admin() {
    local admin_user="$1"
    local admin_pass="$2"
    "${PYTHON_BIN}" - "${WEB_ENV_FILE}" "${admin_user}" "${admin_pass}" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
updates = {
    'FETCHLINKS_ADMIN_USER': sys.argv[2],
    'FETCHLINKS_ADMIN_PASS': sys.argv[3],
}

lines = path.read_text(encoding='utf-8').splitlines() if path.exists() else []
seen = set()
out = []

for line in lines:
    stripped = line.strip()
    if stripped and not stripped.startswith('#') and '=' in line:
        key = line.split('=', 1)[0].strip()
        if key in updates:
            out.append(f'{key}={updates[key]}')
            seen.add(key)
            continue
    out.append(line)

for key, value in updates.items():
    if key not in seen:
        out.append(f'{key}={value}')

path.write_text('\n'.join(out) + '\n', encoding='utf-8')
PY
    chown "${APP_USER}:${APP_GROUP}" "${WEB_ENV_FILE}"
    chmod 0600 "${WEB_ENV_FILE}"
}

configure_web_admin_if_missing() {
    local admin_user=""
    local admin_pass=""
    local generated_password=""

    if [[ -f "${WEB_ENV_FILE}" ]]; then
        printf '  ok - web environment exists at %s\n' "${WEB_ENV_FILE}"
        return 0
    fi

    log "Configuring web admin credentials"
    install -o "${APP_USER}" -g "${APP_GROUP}" -m 0600 \
        "${WEB_DIR}/.env.production.example" "${WEB_ENV_FILE}"

    read -r -p "Admin username [admin]: " admin_user
    admin_user="${admin_user:-admin}"

    generated_password="$(generate_admin_password)"
    printf 'Admin password [press Enter to generate a strong password, or type one]: '
    IFS= read -r -s admin_pass || true
    printf '\n'
    if [[ -z "${admin_pass}" ]]; then
        admin_pass="${generated_password}"
        GENERATED_ADMIN_PASSWORD="${generated_password}"
    fi

    set_web_env_admin "${admin_user}" "${admin_pass}"
}

list_enabled_ingest_sources() {
    "${PYTHON_BIN}" - "${CONFIG_FILE}" <<'PY'
from pathlib import Path
import sys
import tomllib

config_path = Path(sys.argv[1])

def as_bool(value, default):
    return bool(default if value is None else value)

with config_path.open('rb') as handle:
    raw = tomllib.load(handle)

sources = raw.get('sources', {})

rss = sources.get('rss') or {}
if as_bool(rss.get('enabled'), True):
    print('rss|RSS')

reddit = sources.get('reddit') or {}
if as_bool(reddit.get('enabled'), True):
    print('reddit|Reddit')

bluesky = sources.get('bluesky') or {}
if as_bool(bluesky.get('enabled'), False):
    print('bluesky|Bluesky')

mastodon = sources.get('mastodon') or {}
if as_bool(mastodon.get('enabled'), False):
    for instance in mastodon.get('instances') or []:
        if as_bool(instance.get('enabled'), True):
            name = instance.get('name') or 'default'
            print(f'mastodon:{name}|Mastodon {name}')
PY
}

write_single_source_config() {
    local source_key="$1"
    local output_path="$2"
    "${PYTHON_BIN}" - "${CONFIG_FILE}" "${source_key}" "${output_path}" <<'PY'
from pathlib import Path
import json
import sys
import tomllib

config_path = Path(sys.argv[1])
source_key = sys.argv[2]
output_path = Path(sys.argv[3])

with config_path.open('rb') as handle:
    raw = tomllib.load(handle)

sources = raw.setdefault('sources', {})
for name, default in (('rss', True), ('reddit', True), ('bluesky', False), ('mastodon', False)):
    section = sources.setdefault(name, {})
    section['enabled'] = name == source_key.split(':', 1)[0]

mastodon = sources.get('mastodon') or {}
for instance in mastodon.get('instances') or []:
    instance['enabled'] = source_key == f"mastodon:{instance.get('name') or 'default'}"

def format_value(value):
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return '[' + ', '.join(format_value(item) for item in value) + ']'
    raise TypeError(f'Unsupported TOML value: {value!r}')

def write_table(lines, header, table):
    if not isinstance(table, dict):
        return
    lines.append(f'[{header}]')
    for key, value in table.items():
        if isinstance(value, dict) or value is None:
            continue
        if isinstance(value, list) and any(isinstance(item, dict) for item in value):
            continue
        lines.append(f'{key} = {format_value(value)}')
    lines.append('')

lines = []
for top_level in ('paths', 'ingest', 'retention'):
    write_table(lines, top_level, raw.get(top_level, {}))

for source in ('rss', 'reddit', 'bluesky', 'mastodon'):
    write_table(lines, f'sources.{source}', sources.get(source, {}))

for instance in (sources.get('mastodon') or {}).get('instances') or []:
    lines.append('[[sources.mastodon.instances]]')
    for key, value in instance.items():
        if value is None:
            continue
        lines.append(f'{key} = {format_value(value)}')
    lines.append('')

output_path.write_text('\n'.join(lines), encoding='utf-8')
PY
    chmod 0644 "${output_path}"
}

write_reddit_seed_config() {
    local output_path="$1"
    local credential_path="$2"
    "${PYTHON_BIN}" - "${CONFIG_FILE}" "${output_path}" "${credential_path}" <<'PY'
from pathlib import Path
import json
import sys
import tomllib

config_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])
credential_path = sys.argv[3]

with config_path.open('rb') as handle:
    raw = tomllib.load(handle)

sources = raw.setdefault('sources', {})
for name in ('rss', 'bluesky', 'mastodon'):
    sources.setdefault(name, {})['enabled'] = False
reddit = sources.setdefault('reddit', {})
reddit['enabled'] = True
reddit['credential_location'] = credential_path

def format_value(value):
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return '[' + ', '.join(format_value(item) for item in value) + ']'
    raise TypeError(f'Unsupported TOML value: {value!r}')

def write_table(lines, header, table):
    if not isinstance(table, dict):
        return
    lines.append(f'[{header}]')
    for key, value in table.items():
        if isinstance(value, dict) or value is None:
            continue
        if isinstance(value, list) and any(isinstance(item, dict) for item in value):
            continue
        lines.append(f'{key} = {format_value(value)}')
    lines.append('')

lines = []
for top_level in ('paths', 'ingest', 'retention'):
    write_table(lines, top_level, raw.get(top_level, {}))
for source in ('rss', 'reddit', 'bluesky', 'mastodon'):
    write_table(lines, f'sources.{source}', sources.get(source, {}))
for instance in (sources.get('mastodon') or {}).get('instances') or []:
    lines.append('[[sources.mastodon.instances]]')
    for key, value in instance.items():
        if value is None:
            continue
        lines.append(f'{key} = {format_value(value)}')
    lines.append('')

output_path.write_text('\n'.join(lines), encoding='utf-8')
PY
    chmod 0644 "${output_path}"
}

seed_source_tables() {
    local rss_seed_config=""
    local reddit_seed_config=""
    local reddit_placeholder=""

    rss_seed_config="$(mktemp "${CONFIG_FILE}.rss-seed.XXXXXX")"
    write_single_source_config "rss" "${rss_seed_config}"
    log "Seeding rss_feeds table from ${RSS_FEEDS_FILE} if empty"
    sudo -u "${APP_USER}" "${VENV_DIR}/bin/python" \
        "${INGEST_DIR}/rss_feed_import.py" \
        --config "${rss_seed_config}" \
        --seed-if-empty "${RSS_FEEDS_FILE}" || \
        warn "rss_feeds seed step reported a failure; check the output above."
    rm -f "${rss_seed_config}"

    reddit_seed_config="$(mktemp "${CONFIG_FILE}.reddit-seed.XXXXXX")"
    reddit_placeholder="$(mktemp)"
    printf '{"reddit": {}}\n' >"${reddit_placeholder}"
    chmod 0644 "${reddit_placeholder}"
    write_reddit_seed_config "${reddit_seed_config}" "${reddit_placeholder}"
    log "Seeding subreddits table from ${SUBREDDITS_FILE} if empty"
    sudo -u "${APP_USER}" "${VENV_DIR}/bin/python" \
        "${INGEST_DIR}/subreddit_import.py" \
        --config "${reddit_seed_config}" \
        --seed-if-empty || \
        warn "subreddits seed step reported a failure; check the output above."
    rm -f "${reddit_seed_config}" "${reddit_placeholder}"
}

validate_ingest_sources() {
    local source_key=""
    local source_label=""
    local temp_config=""
    local temp_output=""
    local found_source=0

    log "Validating enabled ingest sources"
    while IFS='|' read -r source_key source_label; do
        [[ -z "${source_key}" ]] && continue
        found_source=1
        temp_config="$(mktemp "${CONFIG_FILE}.validate.XXXXXX")"
        temp_output="$(mktemp)"
        write_single_source_config "${source_key}" "${temp_config}"

        if sudo -u "${APP_USER}" "${VENV_DIR}/bin/python" \
            "${INGEST_DIR}/fetch_links.py" --config "${temp_config}" \
            >"${temp_output}" 2>&1; then
            printf '  ok - %s\n' "${source_label}"
        else
            VALIDATION_FAILED_SOURCES+=("${source_label}")
            warn "${source_label} failed validation. Recent output:"
            tail -n 20 "${temp_output}" >&2 || true
        fi

        rm -f "${temp_config}" "${temp_output}"
    done < <(list_enabled_ingest_sources)

    if [[ "${found_source}" -eq 0 ]]; then
        warn "No enabled ingest sources found to validate."
    fi
}

# ---- sanity -----------------------------------------------------------------

if [[ $EUID -ne 0 ]]; then
    echo "Run as root (sudo $0)." >&2
    exit 1
fi

if ! grep -q '^ID=ubuntu' /etc/os-release; then
    warn "Not Ubuntu; tested only on Ubuntu 24.04. Continuing anyway."
fi

# ---- swap -------------------------------------------------------------------
# A 2 GB VM (B1ms) has little/no swap by default, and `next build` can spike
# memory enough to OOM. Ensure a swap file exists before the web build.

SWAP_FILE="/swapfile"
SWAP_SIZE="2G"

if ! swapon --show --noheadings | grep -q "${SWAP_FILE}"; then
    log "Setting up ${SWAP_SIZE} swap file at ${SWAP_FILE}"
    if [[ ! -f "${SWAP_FILE}" ]]; then
        fallocate -l "${SWAP_SIZE}" "${SWAP_FILE}" || \
            dd if=/dev/zero of="${SWAP_FILE}" bs=1M count=2048
    fi
    chmod 600 "${SWAP_FILE}"
    if ! file "${SWAP_FILE}" 2>/dev/null | grep -q 'swap file'; then
        mkswap "${SWAP_FILE}" >/dev/null
    fi
    swapon "${SWAP_FILE}"
    if ! grep -q "^${SWAP_FILE} " /etc/fstab; then
        echo "${SWAP_FILE} none swap sw 0 0" >>/etc/fstab
    fi
else
    log "Swap already active at ${SWAP_FILE}; skipping"
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

# ---- first-install interactive setup ----------------------------------------

configure_missing_credentials
configure_web_admin_if_missing

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

# ---- one-time source table seeds. These use temporary seed-only configs so
# missing network credentials do not block no-network seed imports.
seed_source_tables

log "Exporting rss_feeds table back to ${RSS_FEEDS_FILE}"
systemctl start fetchlinks-export-rss-feeds.service || \
    warn "rss_feeds export step reported a failure; check the journal."

validate_ingest_sources

# nginx + TLS are provisioned separately by deploy/tls.sh.

# ---- final summary ----------------------------------------------------------

log "Done. Status:"
systemctl --no-pager --lines=0 status fetchlinks-web.service                  || true
systemctl --no-pager --lines=0 status fetchlinks-ingest.timer                 || true
systemctl --no-pager --lines=0 status fetchlinks-retain.timer                 || true
systemctl --no-pager --lines=0 status fetchlinks-export-rss-feeds.timer       || true

cat <<EOF

------------------------------------------------------------
Bootstrap finished.

Optional next step: provision nginx + TLS once DNS points at this VM:
  sudo FETCHLINKS_DOMAIN=fetchlinks.example.com \
       FETCHLINKS_EMAIL=you@example.com \
       ${APP_DIR}/deploy/tls.sh
------------------------------------------------------------
EOF

if [[ -n "${GENERATED_ADMIN_PASSWORD}" ]]; then
    cat <<EOF

Generated web admin password (shown once):
  ${GENERATED_ADMIN_PASSWORD}
EOF
fi

if [[ "${#SKIPPED_CREDENTIAL_SOURCES[@]}" -gt 0 ]]; then
    warn "Skipped credentials for: ${SKIPPED_CREDENTIAL_SOURCES[*]}. Those sources remain enabled and may fail until credentials are added."
fi

if [[ "${#VALIDATION_FAILED_SOURCES[@]}" -gt 0 ]]; then
    warn "Install completed, but these sources failed validation: ${VALIDATION_FAILED_SOURCES[*]}."
fi
