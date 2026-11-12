# chainsout

Busca ofertas de trabajo en varias fuentes, las filtra con Claude según los criterios que
escribas en `profile.yaml` (dominio, ubicación/remoto, salario, preferencias...) y avisa por
Telegram de las que encajan. Uso personal, headless, pensado para ejecutarse cada pocas horas.

```
chainsout run
  1. fetch      LinkedIn/Indeed (JobSpy), HN "Who is hiring", bolsas de nicho, ATS de empresas
  2. normalize  modelo Job común, URL canónica, fingerprint empresa+título
  3. dedup      SQLite: URL ya vista → se ignora; misma oferta en otra fuente → "duplicate"
  4. prefilter  reglas baratas de profile.yaml (títulos obvios, part-time, sin keywords de dominio, empresas excluidas)
  5. classify   Claude (claude -p con tu suscripción), salida JSON validada con pydantic
  6. notify     Telegram si match, o maybe con score >= UMBRAL_MAYBE
```

## Instalación

```bash
uv sync                 # (uv sync --extra api si quieres el backend anthropic_api)
cp .env.example .env    # y rellena lo que haga falta
cp examples/<el-más-parecido-a-ti>/*.yaml .   # y adáptalo; tu config no va a git
```

### Telegram
1. Crea un bot con @BotFather y pon el token en `TELEGRAM_BOT_TOKEN` (`.env`).
2. Escribe cualquier cosa al bot y ejecuta `uv run chainsout telegram-setup`: imprime la línea
   `TELEGRAM_CHAT_ID=...` para copiar en `.env`.

Sin Telegram configurado, `run` funciona igual y deja las ofertas pendientes de enviar.

### LLM
- `LLM_BACKEND=claude_cli` (por defecto): usa `claude -p` (`CLAUDE_BIN`) con la sesión ya iniciada
  de Claude Code; consume cuota de la suscripción. Sin herramientas, sin sesión persistente y con
  `--json-schema` para forzar la salida.
- `LLM_BACKEND=anthropic_api`: SDK `anthropic` con `ANTHROPIC_API_KEY` (facturación aparte).
- `LLM_MODEL` por defecto `claude-opus-5-5`. `claude-haiku-5-5` gasta mucha menos cuota y
  probablemente baste; es decisión tuya.
- `CLASSIFY_BATCH` ofertas por llamada (5). `UMBRAL_MAYBE` (6).

## Uso

```bash
uv run chainsout run --dry-run        # todo menos enviar; imprime tabla de lo que se enviaría
uv run chainsout run                  # ejecución normal (idempotente)
uv run chainsout run -s hn -s companies --limit 20
uv run chainsout fetch                # solo descargar + dedup + prefiltro (sin LLM)
uv run chainsout classify --limit 10
uv run chainsout notify [--dry-run]
uv run chainsout stats --days 7
uv run chainsout show <id>            # oferta + veredicto
uv run chainsout feedback <id> interesa|no_interesa
uv run chainsout summary [--send]     # resumen semanal (se envía solo cada 7 días en `run`)
uv run chainsout daemon               # bucle programado (alternativa a cron)
```

### Programación
Con cron (máquina con cron):

```
0 8-22/3 * * * cd /ruta/chains-out && ~/.local/bin/uv run chainsout run >> run.log 2>&1
```

Sin cron (p. ej. dentro de un contenedor): `chainsout daemon` ejecuta `run` cada 3 h entre las
8 y las 22 (hora de `--tz`, por defecto `$TZ` o UTC). Lánzalo desacoplado:

```bash
setsid nohup uv run chainsout daemon >> daemon.log 2>&1 &
```

No sobrevive a un reinicio del contenedor/máquina: hay que volver a lanzarlo.

### GitHub Actions (sin máquina propia)
`.github/workflows/chainsout.yml` ejecuta el pipeline 6 veces al día (también a mano, con
"Run workflow"; el modo `test-notify` solo prueba Telegram).

- **Config**: en el secret `CHAINSOUT_CONFIG`, un YAML con las secciones `profile`, `searches` y
  `companies` (si la variable existe, la herramienta no lee los `.yaml` locales).
  `./subir-config.sh` la monta a partir de tus ficheros y sube también `TELEGRAM_BOT_TOKEN`,
  `TELEGRAM_CHAT_ID` y `CHAINSOUT_DB_KEY` (de `.env`; la genera si falta).
- **Claude**: `claude setup-token` (usa tu suscripción) y
  `gh secret set CLAUDE_CODE_OAUTH_TOKEN`.
- **Opcional**: variables del repo `LLM_MODEL`, `UMBRAL_MAYBE`, `CHAINSOUT_ENABLED` (`false`
  lo para todo) y `CLAUDE_TOKEN_EXPIRES` (`AAAA-MM-DD`: avisa por Telegram los últimos 30 días;
  los tokens de `claude setup-token` duran un año). Si Claude se queda sin uso
  disponible o rechaza el token, chainsout para en la primera llamada, avisa una vez por
  Telegram (😴 / 🔑), deja las ofertas en cola sin gastar sus intentos y duerme hasta la hora
  de reset (o un par de horas); al volver avisa con ✅. Si falla en todas las ofertas por
  otra cosa, el paso falla y llega el aviso de fallo.
- **Estado**: la base de datos se guarda cifrada (AES-256, clave `CHAINSOUT_DB_KEY`) en la caché
  de Actions entre ejecuciones. GitHub borra cachés sin uso en 7 días: si el workflow se para
  más de una semana, empieza de cero (y puede repetir avisos).
- **Logs**: en un repo público los ve cualquiera, así que cada paso solo imprime recuentos.
  Si algo falla, llega un aviso por Telegram con el enlace a la ejecución.
- **Secrets**: cada uno se da solo al paso que lo usa; los forks y las PR de terceros no los
  reciben. Acciones de terceros fijadas por SHA y dependencias por `uv.lock`.
- LinkedIn/Indeed bloquean más desde IPs de centros de datos: espera menos volumen de ahí.
- No uses Actions y el daemon a la vez: los dos leerían el feedback de Telegram.

### Pausa
Escribe `/pausar` al bot (p. ej. cuando encuentres trabajo) y `/reanudar` para volver; `/estado`
dice cómo está. Se recoge al inicio de la siguiente ejecución. Con `CHAINSOUT_ENABLED=false`
(en `.env` o variable del repo) se para igual.

### Feedback
Cada aviso lleva botones `👍 Me interesa` / `👎 No`. No hay webhook ni servidor: las pulsaciones
se recogen con `getUpdates` al principio de cada `run` (o con `chainsout poll-feedback`), se
guardan en la tabla `feedback` y los botones del mensaje se sustituyen por lo elegido. Hasta la
siguiente ejecución Telegram no confirma la pulsación; es normal.

El resumen semanal (vistas, prefiltradas y por qué, motivos de descarte del LLM, notificadas,
feedback) se envía solo cada 7 días desde `run`; `chainsout summary [--send]` lo genera a mano.

Tu configuración (`profile.yaml`, `searches.yaml`, `companies.yaml`) es local y está en `.gitignore`;
en el repo solo van ejemplos, en `examples/` (embedded/Rust en España, frontend en Portugal,
datos en Alemania, DevOps en México, ML en UK, seguridad en Polonia). Sin `companies.yaml`, la
fuente de páginas de empresa se salta.

## Configuración
- `profile.yaml`: criterios (van tal cual al prompt) y reglas del prefiltro. Es la fuente de verdad.
- `profile.yaml` → `company_reject`: empresas excluidas (regex contra el nombre de la empresa); se descartan antes del LLM.
- `searches.yaml`: términos × ubicaciones × fuentes, `hours_old`, pausas.
- `companies.yaml`: empresas con página de empleo en Greenhouse / Lever / Ashby.
- `chainsout/prompts/classify.md`: prompt del clasificador (si lo cambias, sube `prompt_version`).

## Fuentes (revisado el 2026-11-12)

| Fuente | Acceso | Condiciones / notas |
|---|---|---|
| LinkedIn | JobSpy (scraping, sin login) | Lo prohíben sus condiciones; bloquea a menudo (429). Volumen bajo |
| Indeed | JobSpy | Ídem; por país con `is_remote` + `fulltime` |
| HN Who is hiring | API Algolia | Pública |
| RemoteOK | `remoteok.com/api` | Pública; piden citar a Remote OK y enlazar a la oferta (el aviso lleva su URL) |
| Remotive | `remotive.com/api/remote-jobs` | Pública; pocas consultas al día (`min_interval_hours: 8`), no republicar |
| We Work Remotely | RSS por categoría | Público |
| Himalayas | `himalayas.app/jobs/api/search` | Pública, con filtro por país (`country: <país>` incluye las worldwide) |
| Greenhouse / Lever / Ashby | APIs JSON públicas de los tableros | Lista de empresas en `companies.yaml` |

Las bolsas generalistas se filtran por título con `boards.keywords` (opcional) de `searches.yaml`;
HN con `hn.keywords` y las páginas de empresa con `title_regex` de `companies.yaml`.

## Tests

```bash
uv run pytest -q              # unitarios (sin red ni LLM)
uv run pytest -q --llm        # + ~15 ofertas etiquetadas a mano contra el LLM (gasta cuota)
```

## Notas
- LinkedIn prohíbe el scraping en sus condiciones y bloquea (HTTP 429) con frecuencia: se registra
  y se sigue con el resto de fuentes. JobSpy está fijado a una versión concreta.
- Secretos solo en `.env` (ignorado por git). Los logs nunca incluyen el token.
