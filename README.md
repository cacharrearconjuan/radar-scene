# Radar de la scene de PS5

Bot de Telegram que avisa de las novedades de la scene de PS5 **que ya están publicadas**: exploits, HEN, payloads, homebrew y firmwares de Sony. No avisa de teasers, rumores ni de cosas que «podrían salir».

## Qué vigila

| Fuente | Qué detecta |
|---|---|
| Repos clave de GitHub (`fuentes.txt`, secciones `[A]` y `[B]`) | Releases nuevas, commits y cambios de firmware en el README |
| Lista oficial de PS5 Payload Manager y otras listas (`[listas]`) | Payloads nuevos y sus releases; los repos se añaden solos |
| Búsqueda en GitHub | Repos de PS5 recién creados que superan 50, 200 o 1.000 estrellas (y tienen código) |
| Web oficial de Sony | Firmwares nuevos de PS5 |
| Prensa (`[prensa]`) | Solo artículos de cosas ya publicadas y con enlace a la descarga |
| YouTube (`[youtube]`) | Si la competencia ya lo ha sacado |

Nunca avisa de descargas de juegos ni de volcados.

## Niveles

- 🔥 **Urgente (9-10):** llega al momento, también de madrugada. Por ejemplo, un exploit para un firmware más alto de lo conocido, una versión nueva de un repo clave o un firmware nuevo de Sony.
- ⭐ **Interesante (6-8)** y 📋 **Menor (3-5):** van al **resumen diario** (09:00, hora de Madrid).
- Por debajo de 3 (solo traducciones, builds automáticas…), nada.

Si la IA (`SUPER_IA_URL`) responde, el aviso lleva un resumen en castellano y un ángulo para el vídeo. Si no responde, el aviso sale igual, marcado «(sin IA)».

## Puesta en marcha

### 1. Bot y grupo de Telegram
1. En Telegram, abre **@BotFather** → `/newbot` → nombre y usuario (tiene que acabar en `bot`). Guarda el **token**.
2. Crea un grupo, añade el bot y escribe en el grupo `/start@usuario_del_bot`.
3. Para saber el **chat_id**: abre en el navegador `https://api.telegram.org/bot<TOKEN>/getUpdates` (con tu token en lugar de `<TOKEN>`). Busca `"chat":{"id":-100…`: ese número, con el signo menos, es el chat_id. Si sale vacío, vuelve a escribir `/start@usuario_del_bot` en el grupo y recarga.

### 2. Secretos del repo
**Settings → Secrets and variables → Actions → New repository secret**:

| Nombre | Valor |
|---|---|
| `TELEGRAM_TOKEN` | el token de @BotFather |
| `TELEGRAM_CHAT_ID` | el chat_id del grupo (con el `-`) |
| `SUPER_IA_URL` | la Base URL de la pasarela de IA, terminada en `/v1` (opcional) |
| `SUPER_IA_KEY` | la clave de la pasarela pensada para GitHub (opcional) |

### 3. Primera prueba
**Actions → radar → Run workflow**:
1. `diagnostico`: manda al grupo qué fuentes responden.
2. `prueba`: manda un aviso de ejemplo.
3. `ronda`: la primera ronda apunta todo lo que ya existe sin avisar y manda «📡 Radar de la scene activado». Desde la segunda, avisa solo de lo nuevo.

### 4. Que se ejecute cada 5 minutos (cron-job.org)
El `schedule` de GitHub va como respaldo cada 15 minutos, pero no es fiable. Lo bueno es cron-job.org:
1. GitHub → avatar → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**. En **Repository access**, **Only select repositories** → este repo. En **Permissions → Actions**: **Read and write**. Genéralo y cópialo.
2. cron-job.org → **Create cronjob**:
   - URL: `https://api.github.com/repos/cacharrearconjuan/radar-scene/actions/workflows/radar.yml/dispatches`
   - Cada 5 minutos.
   - Pestaña **Advanced**:
     - Método: **POST**.
     - Cabeceras: `Authorization: Bearer <token>`, `Accept: application/vnd.github+json` y `Content-Type: application/json`.
     - Cuerpo: `{"ref":"main","inputs":{"modo":"ronda"}}`.
3. Guarda. Una respuesta **204** es que va bien.

## Ajustes

- **`fuentes.txt`**: añadir o quitar repos, listas, canales de YouTube o prensa (una línea cada uno).
- **`config.json`**:
  - `hora_resumen` y `silencio`: horarios.
  - `fuego_rompe_silencio`: si los 🔥 llegan de madrugada.
  - `umbral_fuego`: nota mínima para avisar al momento.
  - `umbrales_estrellas`: estrellas que tiene que alcanzar un repo nuevo.
  - `ia.modelo`: modelo de la pasarela de IA.
- **Estado:** el bot guarda lo que ya ha visto en la rama `estado` (`estado.json`). Para empezar de cero, borra esa rama.

## Pruebas

```
python3 -m unittest discover -s tests -t .
```
