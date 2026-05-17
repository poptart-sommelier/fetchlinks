#!/usr/bin/env bash
# Thin wrapper for ansible-playbook. Usage:
#   deploy/scripts/deploy.sh            # full playbook
#   deploy/scripts/deploy.sh --check    # dry-run
#   deploy/scripts/deploy.sh --tags common,python
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANSIBLE_DIR="${SCRIPT_DIR}/../ansible"

cd "${ANSIBLE_DIR}"
exec ansible-playbook -i inventory.yml site.yml "$@"
