# Alertas meteorológicas CABA

**GCBA · Jefatura de Gabinete de Ministros**

Sistema automático de monitoreo y alertas meteorológicas para la Ciudad de Buenos Aires:

- **Bot de Telegram** que publica un aviso cuando aparece una alerta, cuando cambia de nivel y cuando termina.
- **Alertas oficiales del SMN**, leídas del feed CAP (Common Alerting Protocol, estándar de la OMM). Se filtran las que tocan alguna de las 15 comunas.
- **Alertas por pronóstico**: el pronóstico horario de Open-Meteo, evaluado con criterios similares a los del Sistema de Alerta Temprana del SMN (amarillo, naranja y rojo).
- **Tablero web** con mapa animado, alertas, tabla de umbrales y conexión al canal de Telegram (con código QR).

Todo corre gratis en GitHub: GitHub Actions ejecuta el proceso (cada 10 minutos con cron-job.org) y GitHub Pages publica el tablero.

---

## Qué hay en la carpeta

| Archivo | Para qué sirve |
|---|---|
| `alertas.py` | El proceso: descarga datos, evalúa umbrales, avisa por Telegram y genera el archivo para el tablero |
| `config.yaml` | **Lo que se edita**: umbrales, horizonte, links del canal y del tablero, comunas |
| `docs/index.html` | El tablero web |
| `.github/workflows/alertas.yml` | La programación: cada 15 minutos corre el proceso y publica el tablero |
| `requirements.txt` | Librerías de Python (requests y PyYAML) |

---

## ¿Tiene que ir a GitHub?

No es obligatorio, pero es lo más simple y no cuesta nada. GitHub sirve para tres cosas:

1. **Ejecutar el proceso cada 15 minutos** sin tener una computadora prendida (GitHub Actions).
2. **Publicar el tablero** con una dirección fija, por ejemplo `https://usuario.github.io/alertas-clima-caba/` (GitHub Pages).
3. **Guardar el historial de cambios** de los umbrales.

Otra opción es correrlo en un servidor del GCBA (ver al final, "Alternativa: servidor propio").

---

## Paso a paso

### Paso 1 · Crear el bot en Telegram (5 minutos)

1. En Telegram, buscá **@BotFather** (tiene tilde azul de verificado) y abrí el chat.
2. Mandá `/newbot`.
3. Elegí un **nombre visible**, por ejemplo `Alertas Clima CABA`.
4. Elegí un **usuario**, que tiene que terminar en `bot`, por ejemplo `AlertasClimaCABA_bot`.
5. BotFather te responde con un **token**, parecido a `7412345678:AAH...`. Guardalo: es la clave del bot.
   **No lo compartas ni lo pegues en ningún archivo del repositorio.**
6. Opcional: mandá `/setcommands`, elegí tu bot y pegá esto:
   ```
   estado - Alertas vigentes en la Ciudad
   umbrales - Criterios que usa el sistema
   ayuda - Lista de comandos
   ```

### Paso 2 · Crear el canal donde publica el bot

1. En Telegram: menú → **Nuevo canal**, por ejemplo "Alertas Clima CABA".
2. Elegí **Público** y un link, por ejemplo `t.me/alertas_clima_caba`. Si lo hacés privado, ver la nota de abajo.
3. Entrá a la configuración del canal → **Administradores** → **Agregar administrador** → buscá tu bot → dale permiso para **publicar mensajes**.

Con un canal público, el identificador es el link con arroba: `@alertas_clima_caba`.

> **Canal privado o grupo:** agregá el bot, mandá cualquier mensaje y abrí en el navegador
> `https://api.telegram.org/bot<TOKEN>/getUpdates` (reemplazá `<TOKEN>`).
> Buscá `"chat":{"id":-100...`: ese número negativo es el identificador.
> Se pueden poner varios destinos separados por coma, por ejemplo `@alertas_clima_caba,-1001234567890`.

### Paso 3 · Crear el repositorio en GitHub

1. Creá una cuenta en [github.com](https://github.com) si no tenés.
2. Arriba a la derecha, **+** → **New repository**.
   - Nombre: `alertas-clima-caba`
   - Visibilidad: **Public**. Es necesario para usar GitHub Pages gratis. El token **no** queda público: va en "secrets".
   - Creá el repositorio.
3. **Subí los archivos**: en el repositorio, **Add file → Upload files**. Arrastrá **todo el contenido** de esta carpeta, sin la carpeta en sí, y tocá **Commit changes**.

> ⚠️ **La carpeta `.github` suele estar oculta** en Windows y Mac, y si no se sube, el proceso no corre.
> Fijate que en el repositorio aparezca `.github/workflows/alertas.yml`. Si no aparece:
> **Add file → Create new file**, escribí como nombre `.github/workflows/alertas.yml`, pegá el contenido del archivo y guardá.

### Paso 4 · Cargar las claves (secrets)

En el repositorio: **Settings → Secrets and variables → Actions → New repository secret**. Creá dos:

| Nombre | Valor |
|---|---|
| `TELEGRAM_BOT_TOKEN` | El token del Paso 1 |
| `TELEGRAM_CHAT_IDS` | El identificador del Paso 2, por ejemplo `@alertas_clima_caba` |

### Paso 5 · Activar GitHub Pages

**Settings → Pages → Build and deployment → Source: GitHub Actions**. No hace falta tocar nada más.

### Paso 6 · Completar `config.yaml`

En GitHub, abrí `config.yaml`, tocá el lápiz (✏️) y completá:

```yaml
general:
  url_tablero: "https://TU-USUARIO.github.io/alertas-clima-caba/"
telegram:
  url_canal: "https://t.me/alertas_clima_caba"
  usuario_bot: "AlertasClimaCABA_bot"
```

Guardá con **Commit changes**. Esto ya dispara una corrida y publica el tablero.

### Paso 7 · Probar

1. Andá a la pestaña **Actions** → **Alertas meteorológicas CABA** → **Run workflow**.
2. Elegí **`prueba`** y tocá **Run workflow**. En menos de un minuto tiene que llegar al canal: *"✅ Bot de alertas conectado"*.
3. Repetí con **`simulacro`**: llega una alerta naranja ficticia, marcada **SIMULACRO**, para ver cómo se ven los mensajes.
4. Abrí el tablero: `https://TU-USUARIO.github.io/alertas-clima-caba/`.

Si algo falla, entrá a la corrida en **Actions** y abrí el paso **"Evaluar alertas y avisar por Telegram"**: ahí se ve el error.

| Error | Causa probable |
|---|---|
| `Unauthorized` | Token mal copiado |
| `chat not found` | El identificador del canal está mal, o el bot no es administrador |
| `Get Pages site failed` | Falta el Paso 5 |

### Paso 8 · Listo

A partir de ahora corre solo cada 15 minutos. El bot avisa únicamente cuando hay algo nuevo:

- 🟡🟠🔴 una alerta **nueva**
- ⬆️ una alerta que **sube** de nivel
- ⬇️ una alerta que **baja** de nivel (se puede desactivar con `avisar_bajas: false`)
- ✅ una alerta que **finaliza**: se da por terminada después de 2 ciclos sin aparecer, para evitar avisos falsos cuando el modelo oscila

Ejemplo de mensaje:

```
🟠 ALERTA NARANJA · Tormentas
📊 Pronóstico · criterios tipo SMN (no es alerta oficial)

🕒 Cuándo: mañana 04:00 a mañana 10:00
📍 Dónde: Comunas 1, 4, 8, 9
🌧️ Detalle: tormentas con posible granizo

Abrir tablero de monitoreo
```

Las alertas oficiales del SMN llegan marcadas **🏛️ Oficial SMN**, con la descripción y las recomendaciones del SMN.

---

## Perspectiva extendida (días 3 a 7)

Además de las alertas (próximas 48 horas), el sistema calcula una **perspectiva** de los días 3 a 7 con los mismos umbrales aplicados a valores diarios: lluvia del día, ráfaga máxima, tormenta, calor y frío.

- **No dispara alertas.** Es un pronóstico de menor certeza: confianza *media* en los días 3 y 4, *baja* del 5 en adelante.
- **Tablero:** pestaña Alertas → "Perspectiva · próximos días". En la lista de 7 días, los días con fenómenos a vigilar llevan un punto de color.
- **Telegram:** un resumen por día, a partir de las 8 h. También a pedido con `/perspectiva`, o desde Actions con el modo `perspectiva`.
- **Configuración** en `config.yaml`, sección `perspectiva`: días, hora del resumen, y si se manda solo cuando hay algo para vigilar (`solo_si_hay_senales: true`).

---

## Seguimiento durante una alerta del SMN

Mientras haya una **alerta oficial del SMN vigente para la Ciudad** (amarilla, naranja o roja), el bot manda **cada 30 minutos un parte** con las próximas 6 horas por comuna (Open-Meteo):

- **Esta hora:** cuántas comunas tienen lluvia y dónde llueve más.
- **Lluvia:** las comunas más afectadas, con milímetros totales, intensidad máxima y a qué hora.
- **Viento:** ráfaga máxima, hora y comunas que superan el umbral.
- **Tormentas:** franja horaria, comunas y posible granizo.
- **Temperatura:** valor actual y mínima de las próximas horas.
- **Tendencia:** si el pronóstico empeoró o mejoró respecto del parte anterior.
- **Hora por hora:** el máximo en la Ciudad.

El seguimiento empieza 3 horas antes del inicio de la alerta del SMN y termina cuando la alerta deja de estar vigente. En ese momento llega "📡 Seguimiento finalizado". Las alertas del SMN, las alertas por pronóstico y el resumen diario se siguen enviando igual.

- **A pedido:** `/ahora` en el bot, o el modo `seguimiento` en Actions para ver un parte de prueba.
- **Tablero:** pestaña Alertas → "Próximas horas por comuna". Cuando hay seguimiento, se ve un aviso "Seguimiento activo".
- **Configuración** en `config.yaml`, sección `seguimiento`: intervalo, horas, niveles del SMN que lo activan, y si también se activa con alertas propias de lluvia, viento o tormenta (`tambien_por_pronostico`).

> GitHub puede demorar las corridas, así que el intervalo real puede ser de 30 a 40 minutos.

---

## Aviso de lluvia (sin alerta)

Aunque no haya ninguna alerta, el bot avisa cuando **llueve en la Ciudad**. Es información, no una alerta:

- 🌂 **Se espera lluvia en la próxima hora:** según el pronóstico por comuna.
- 🌦️ **Está lloviendo:** dónde, con qué intensidad, lo **observado en Aeroparque** (METAR) y hasta qué hora seguiría.
- 🌧️ **Sigue lloviendo:** un parte breve cada 60 minutos mientras dure.
- 🌤️ **Dejó de llover.**

Combina dos fuentes: la observación real de las estaciones aeronáuticas (Aeroparque, San Fernando, El Palomar y Ezeiza, vía aviationweather.gov) y el pronóstico de Open-Meteo para cada comuna. Si hay seguimiento por una alerta del SMN, estos avisos se silencian para no duplicar mensajes. La configuración está en `config.yaml`, sección `aviso_lluvia`.

---

## Que corra seguido: cron-job.org (recomendado)

GitHub no garantiza los horarios programados: en momentos de mucha carga demora o saltea corridas. La solución gratuita es que un servicio externo le pida a GitHub que corra el proceso cada 10 minutos.

**1. Crear un token en GitHub (solo para este repositorio)**

1. github.com → tu foto → **Settings** → abajo a la izquierda, **Developer settings** → **Personal access tokens** → **Fine-grained tokens** → **Generate new token**.
2. Completá:
   - **Token name:** `cron-job alertas`
   - **Expiration:** 1 año (o el máximo que permita)
   - **Repository access:** *Only select repositories* → `AlertasPrueba1`
   - **Permissions → Repository permissions → Actions:** *Read and write*
3. **Generate token** y copialo. Empieza con `github_pat_` y se muestra una sola vez.

**2. Configurar cron-job.org**

1. Creá una cuenta gratis en [cron-job.org](https://cron-job.org) → **Create cronjob**.
2. **URL:**
   `https://api.github.com/repos/gastonperezalfarogcba-hub/AlertasPrueba1/actions/workflows/alertas.yml/dispatches`
3. **Execution schedule:** *Every 10 minutes*.
4. En **Advanced**:
   - **Request method:** `POST`
   - **Headers** (agregá los tres):
     - `Authorization` = `Bearer github_pat_...` (tu token)
     - `Accept` = `application/vnd.github+json`
     - `Content-Type` = `application/json`
   - **Request body:** `{"ref":"main","inputs":{"modo":"normal"}}`
5. Guardá y tocá **Test run**: tiene que responder **204**. En GitHub → Actions va a aparecer una corrida nueva.

El cron de GitHub queda como respaldo. Cuando el token venza, generá uno nuevo y reemplazalo en cron-job.org.

---

## Cambiar los umbrales

Todo está en `config.yaml`, sección `umbrales`. Editás, guardás, y el próximo ciclo ya usa los valores nuevos. El tablero muestra la misma tabla, así que el bot y el tablero siempre coinciden.

| Fenómeno | 🟡 Amarillo | 🟠 Naranja | 🔴 Rojo | Base |
|---|---|---|---|---|
| Lluvia acumulada | 40 mm / 12 h | 80 mm / 12 h | 150 mm / 24 h | SMN (región Llanura) |
| Intensidad de lluvia | 20 mm / 1 h | 35 mm / 1 h | 60 mm / 1 h | Criterio local (drenaje urbano) |
| Viento medio | 55 km/h | 75 km/h | 90 km/h | SMN Llanura, mínimo 2 h |
| Ráfagas | 65 km/h | 90 km/h | 110 km/h | SMN Llanura, mínimo 2 h |
| Tormenta | Tormenta pronosticada | + 60 mm/12 h, ráfagas de 90 km/h o granizo | + 100 mm/12 h o ráfagas de 110 km/h | Lógica SMN (intensidad, ráfagas, granizo) |
| Calor | 2 días con Tmáx ≥ 32,3 °C y Tmín ≥ 22 °C | 3 días | 3 o más días y Tmáx ≥ 38 °C | Umbrales de Buenos Aires: verificar con el SMN |
| Frío | Tmín ≤ 2 °C | ≤ 0 °C | ≤ −3 °C | Referencia |

### De dónde salen los umbrales

- El SMN define amarillo y naranja de lluvia por **acumulado en 12 horas** y rojo por **24 horas**. Para viento usa **viento medio y ráfagas con duración mínima**. Los valores parten de percentiles climáticos (P90, P95 y P99) por región. Ver [Nota Técnica SMN 2021-109](https://repositorio.smn.gob.ar/bitstream/handle/20.500.12160/1723/Nota_Tecnica_SMN_2021-109.pdf) y [Umbrales para los alertas](https://repositorio.smn.gob.ar/bitstream/handle/20.500.12160/2592/C4-%20Umbrales%20para%20los%20alertas%20P%C3%BAblico.pdf).
- El SMN también pondera factores que un modelo no ve, como suelos saturados o la probabilidad de ocurrencia. Por eso las alertas "por pronóstico" son una **herramienta de monitoreo interno** y **no reemplazan las alertas oficiales**. Los mensajes lo aclaran.
- Conviene validar los valores con el área técnica: Defensa Civil o el SMN.

---

## Cómo funciona por dentro

```
cada 15 min (GitHub Actions)
   │
   ├─ Open-Meteo ──► pronóstico horario de las 15 comunas ──► evaluación con umbrales
   ├─ SMN (CAP) ───► alertas oficiales vigentes ──► ¿el polígono toca alguna comuna?
   │
   ├─ compara con el ciclo anterior (estado guardado en la caché de Actions)
   ├─ Telegram ────► avisa solo lo nuevo · responde /estado /umbrales /ayuda
   └─ docs/data/alertas.json ──► GitHub Pages ──► tablero
```

- **Alertas del SMN:** el proceso lee `https://ssl.smn.gob.ar/CAP/AR.php`, descarga cada alerta CAP (una sola vez) y comprueba si su polígono contiene el centro de alguna comuna. La severidad CAP se traduce a colores: Moderate = amarillo, Severe = naranja, Extreme = rojo.
- **El tablero** calcula las alertas por pronóstico en el momento con la misma lógica, y lee del archivo `alertas.json` las alertas oficiales del SMN, los umbrales y el último aviso enviado.
- **Los comandos del bot** se responden en cada ciclo, así que la respuesta puede demorar hasta 15 minutos.

---

## Probar en tu computadora (opcional)

```bash
pip install -r requirements.txt
python alertas.py --dry-run              # muestra los mensajes sin enviarlos
python alertas.py --dry-run --modo simulacro

# Con envío real:
export TELEGRAM_BOT_TOKEN="7412345678:AAH..."      # en Windows: set TELEGRAM_BOT_TOKEN=...
export TELEGRAM_CHAT_IDS="@alertas_clima_caba"
python alertas.py --modo prueba
```

Para ver el tablero en tu computadora con los datos del proceso: `cd docs && python -m http.server`, y abrí `http://localhost:8000`.

---

## Límites a tener en cuenta

- **Demoras de GitHub:** las ejecuciones programadas pueden atrasarse de 5 a 15 minutos en horas de mucha demanda. Para alertas críticas en tiempo real conviene un servidor propio.
- **Inactividad:** en repositorios públicos, GitHub **pausa las tareas programadas después de 60 días sin cambios** en el repositorio, y avisa por mail antes. Se reactivan desde Actions con un clic, o haciendo cualquier cambio (por ejemplo, en un umbral).
- **Open-Meteo** es gratis para uso no comercial, hasta 10.000 consultas por día; este sistema usa unas 1.500. Para uso institucional formal, conviene consultarles (info@open-meteo.com) o instalar su servidor propio, que es de código abierto.
- **Feed CAP del SMN:** es público. Si el SMN cambia el formato, el proceso sigue funcionando con Open-Meteo y muestra el error en el tablero.
- **Seguridad:** si el token se filtra, en @BotFather usá `/revoke` y cargá el nuevo en los secrets.

---

## Alternativa: servidor propio (GCBA)

En cualquier servidor Linux con Python 3.10 o superior:

```bash
pip install -r requirements.txt
# crontab -e
*/10 * * * * cd /opt/alertas-clima-caba && TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_IDS=... /usr/bin/python3 alertas.py >> alertas.log 2>&1
```

Después, publicá la carpeta `docs/` con cualquier servidor web (nginx o Apache). El estado queda en `estado/estado.json`.

---

## Fuentes

- Pronóstico: [Open-Meteo](https://open-meteo.com) (CC BY 4.0)
- Alertas oficiales: [Servicio Meteorológico Nacional](https://www.smn.gob.ar/alertas), feed CAP
- Radar: [RainViewer](https://www.rainviewer.com) · Satélite: NASA GIBS / NOAA GOES-East
- Mapas: [OpenFreeMap](https://openfreemap.org) y Esri, © colaboradores de OpenStreetMap
