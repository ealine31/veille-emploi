#!/bin/bash
# Wrapper appelé par cron — charge les variables et lance le script Python
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Charger le fichier .env
if [[ -f "$SCRIPT_DIR/.env" ]]; then
    set -o allexport
    # shellcheck disable=SC1091
    source "$SCRIPT_DIR/.env"
    set +o allexport
fi

exec /usr/local/bin/python3 "$SCRIPT_DIR/recherche_emploi.py" "$@"
