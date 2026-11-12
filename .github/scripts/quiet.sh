#!/usr/bin/env bash
# Ejecuta un comando de chainsout sin exponer nada en el log público de Actions:
# solo imprime las líneas de recuento y cuántos avisos/errores hubo.
set -uo pipefail
log="$(mktemp)"
"$@" >"$log" 2>&1
rc=$?
grep -oE '(resumen|ingest|prefiltro|clasificación|notificación): .*' "$log" || true
grep -oE '^[0-9]+ respuestas nuevas' "$log" || true
# Errores del CLI de Claude (sin datos de ofertas): ayudan a ver si el token ha caducado.
grep -oE 'claude (error|rc=[0-9]+|timeout)[^"]{0,160}' "$log" | sort | uniq -c | sort -rn | head -n 3 || true
echo "avisos=$(grep -cE '^[0-9-]+ [0-9:]+ W ' "$log") errores=$(grep -cE '^[0-9-]+ [0-9:]+ E ' "$log") rc=$rc"
if [ $rc -ne 0 ]; then
  grep -oE '^[A-Za-z.]*(Error|Exception|Exit)[A-Za-z]*' "$log" | tail -n 1 || true
fi
rm -f "$log"
exit $rc
