#!/usr/bin/env bash
# Sube la configuración local a los secrets del repo para GitHub Actions:
#   CHAINSOUT_CONFIG    profile.yaml + searches.yaml + companies.yaml en un solo YAML
#   TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   (de .env)
#   CHAINSOUT_DB_KEY    clave para cifrar la base de datos en la caché (se genera en .env si falta)
# CLAUDE_CODE_OAUTH_TOKEN va aparte: `claude setup-token` y `gh secret set CLAUDE_CODE_OAUTH_TOKEN`.
# Uso: ./subir-config.sh [-R dueño/repo]
set -euo pipefail
cd "$(dirname "$0")"
repo_args=("$@")

for f in profile searches; do
  [ -s "$f.yaml" ] || { echo "Falta $f.yaml (cópialo de examples/)" >&2; exit 1; }
done
[ -f .env ] || { echo "Falta .env" >&2; exit 1; }

get_env() { grep -E "^$1=" .env | tail -n 1 | cut -d= -f2- || true; }

if [ -z "$(get_env CHAINSOUT_DB_KEY)" ]; then
  echo "CHAINSOUT_DB_KEY=$(openssl rand -hex 32)" >> .env
  echo "Generada CHAINSOUT_DB_KEY en .env (guárdala: sin ella el estado en la caché no se puede leer)"
fi

tmp="$(mktemp)"; trap 'rm -f "$tmp"' EXIT
uv run --no-sync python - "$tmp" <<'PY'
import sys, yaml
from pathlib import Path
data = {k: (yaml.safe_load(Path(f"{k}.yaml").read_text(encoding="utf-8"))
            if Path(f"{k}.yaml").exists() else None) or {}  # companies.yaml es opcional
        for k in ("profile", "searches", "companies")}
Path(sys.argv[1]).write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
PY
# Comprobación: la herramienta lee bien la config desde la variable de entorno.
CHAINSOUT_CONFIG="$(cat "$tmp")" uv run --no-sync python -c "
from pathlib import Path
from chainsout.config import load_yaml
from chainsout.prefilter import Prefilter
Prefilter(load_yaml(Path('profile.yaml'))); load_yaml(Path('searches.yaml')); load_yaml(Path('companies.yaml'))"

gh secret set CHAINSOUT_CONFIG "${repo_args[@]}" < "$tmp"
for k in TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID CHAINSOUT_DB_KEY; do
  v="$(get_env $k)"
  [ -n "$v" ] || { echo "Falta $k en .env" >&2; exit 1; }
  printf '%s' "$v" | gh secret set "$k" "${repo_args[@]}"
done
echo "Secrets subidos. Falta CLAUDE_CODE_OAUTH_TOKEN si no lo has puesto ya."
