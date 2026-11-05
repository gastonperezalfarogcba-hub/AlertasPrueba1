# Cambios

## Actualización 8 · noviembre 2026

### Perspectiva dos veces por día
- La perspectiva de los días 3 a 7 se manda **a las 8 y a las 20 h**. El segundo envío dice "actualización de la tarde".
- Las horas se cambian en `config.yaml`, en `perspectiva → hora_resumen`. Puede ser una lista, como `[8, 20]`, o una sola hora, como `8`.
- Si una corrida llega tarde, por ejemplo a las 21 h sin haber mandado el de las 8, se manda uno solo, no dos seguidos.

### Tablero
- **Celular:** "Mapa" deja la hoja cerrada. Queda solo la barrita, con una línea de estado ("Sin alertas en la Ciudad" o la alerta vigente), y el mapa casi completo. Así abre el tablero.
- **Computadora:** al tocar una pestaña, el panel se desplaza para mostrar su contenido enseguida.
- **Computadora:** si el tablero se abre con la ventana angosta y después se agranda, se marca la pestaña correcta.
- **Telegram:** se corrigió el contraste de los botones "Unirme al canal" y "Abrir el bot".
- **Celular:** la barra de tiempo tiene un área táctil de 44 px.


## Actualización 6 · noviembre 2026 · integración y seguridad

Revisión de las actualizaciones 3 a 5 antes de subirlas. Se probó todo con datos simulados: alertas, fin de alertas, seguimiento, avisos de lluvia, perspectiva, comandos, WRF y tablero (escritorio y celular).

- **Horario de respaldo:** se vuelve a los minutos 7, 22, 37 y 52. El disparo principal sigue siendo cron-job.org cada 10 minutos.
- **Formato rechazado por Telegram:** si Telegram no acepta el formato del mensaje, se reenvía como texto simple en vez de perderse.
- **Errores aislados:** si falla el seguimiento, el resumen diario, el aviso de lluvia o los comandos, el resto del ciclo sigue y el tablero se publica igual.
- **Mensajes más cortos (un 30 % menos):**
  - La probabilidad va en la misma línea que la fuente: "confianza media (52 %)".
  - Si casi ningún escenario la confirma, se dice en palabras ("confianza baja: los escenarios no lo confirman") en lugar de mostrar 0 %.
  - La comparación entre modelos ocupa una sola línea.
  - En las alertas del SMN, las recomendaciones van resumidas, con enlace a la alerta completa.
  - En el parte de seguimiento se sacaron el párrafo explicativo y las líneas sin novedad, y se muestran 4 comunas en vez de 6.

## Actualización 5 · noviembre 2026 · mapa

- **Celular:** el tablero abre con la hoja baja, es decir, el mapa con el estado de la Ciudad abajo. Se sube para leer.
- **Límites de comunas bien visibles:** borde blanco con contorno oscuro, por encima de las capas del clima. La comuna elegida se marca en amarillo.
- **Los límites vienen incluidos en `docs/index.html`,** así que ya no dependen de que se suba `docs/comunas.geojson`. Ese archivo puede quedar en el repositorio como referencia.
- **Calles y avenidas al 50 %** de visibilidad, líneas y nombres, en todos los mapas vectoriales. Los mapas de Esri son imágenes y no permiten separar las calles.


## Actualización 4 · noviembre 2026 · celular

- **Hoja inferior en celular:** el mapa queda siempre de fondo, y el panel es una hoja con tres alturas.
  - **Media** (al entrar): mapa arriba, estado y resumen abajo.
  - **Baja:** solo el estado; el mapa casi completo, con la barra de tiempo y la leyenda.
  - **Alta:** el panel completo para leer.
- **Cómo se mueve:** se arrastra desde la barrita o desde el estado, o se toca la barrita para subirla.
- **Pestañas fijas abajo:** **Mapa** baja la hoja y las demás la suben.
- **El mapa se encuadra** en el espacio que deja la hoja. Si lo movés a mano, se queda donde lo dejaste.
- **Si el sistema pide reducir el movimiento,** la hoja cambia de altura sin animación.


## Versión 3.3 · noviembre 2026 · tablero

Mejoras aprobadas en la evaluación de visualización: 13 de 15. Quedaron afuera las animaciones accesibles y el modo claro.

### Celular
- **El panel ocupa la pantalla:** primero el estado y el resumen. El mapa pasa a su propia pestaña **Mapa**, y las pestañas quedan fijas abajo.
- **La tabla por comuna** mantiene los títulos de las columnas al desplazarse.
- **Botones de 44 px**, texto de 12 px como mínimo y un desvanecido en las filas que se deslizan (capas y próximas 24 horas).

### Todos los tamaños
- **Resumen operativo** arriba del panel: próximas 6 horas y una línea por alerta, con cuándo, qué, comunas con más lluvia y un chip de confianza.
- **Límites oficiales de las 15 comunas** en el mapa (`docs/comunas.geojson`, datos abiertos del GCBA), pintados según la capa elegida. En lluvia usa el WRF del SMN cuando está disponible. Se toca una comuna para seleccionarla.
- **Pines compactos:** número de comuna con el color del nivel de alerta; el valor aparece solo en la comuna elegida.
- **El mapa encuadra la Ciudad** en el espacio libre, con zoom intermedio.
- **Vista inicial "Toda la Ciudad":** el peor valor de las 15 comunas, con la comuna más afectada.
- **Lenguaje sin nombres técnicos:** modelos "europeo, estadounidense y alemán", columnas "WRF" y "Global".
- **La tarjeta del SMN** ya no repite el nivel en el título.
- **Enlaces directos:** `#alertas`, `#comunas`, `#perspectiva`, `#mapa`, `#ciudad`, `#comuna-8`. Los mensajes del bot enlazan a la vista que corresponde.


## Versión 3.2 · noviembre 2026 · unificación

Junta la v3.1 con la "actualización 2". Las dos partían de la actualización 1.

### Incorporado de la actualización 2
- **Aviso de lluvia sin alerta:** avisa cuando se espera lluvia en la próxima hora, cuando llueve, mientras sigue (cada 60 minutos) y cuando para. Usa las observaciones de Aeroparque, San Fernando, El Palomar y Ezeiza (METAR) y el pronóstico por comuna. Sección `aviso_lluvia` en `config.yaml`.
- **Tablero:** línea "Observado en Aeroparque" y estado "está lloviendo".
- **Comandos** también desde canales de Telegram.
- **README:** instrucciones de cron-job.org para correr cada 10 minutos.

### Ajustes al integrarlo
- El aviso dice "Está lloviendo" solo si alguna estación lo **observa**. Si lo indica únicamente el pronóstico, dice "Lluvia probable en este momento (según el pronóstico, sin confirmar)".
- Si un aviso de lluvia no llega por Telegram, se reintenta en el ciclo siguiente, igual que las alertas.


## Versión 3.1 · noviembre 2026

### Nuevo: modelo WRF-SMN de 4 km para la lluvia por comuna
- `wrf.py` y el workflow **WRF-SMN** (`wrf.yml`) descargan una vez por hora el modelo de alta resolución del SMN (datos abiertos en AWS) y lo resumen por comuna. Leen solo el recorte de CABA, no el mapa completo.
- Las alertas de lluvia combinan Open-Meteo y WRF-SMN. El mensaje muestra lo que dice cada modelo, si coinciden, y las comunas con más lluvia según el WRF.
- Los partes de seguimiento y `/ahora` usan el WRF para la lluvia por comuna.
- El tablero suma una columna WRF en "Próximas horas por comuna".
- Configuración en `config.yaml`, sección `wrf`.

### Qué hay que hacer al subir esta versión
1. Subir los archivos nuevos: `wrf.py`, `requirements-wrf.txt` y `.github/workflows/wrf.yml`, además de los que cambiaron.
2. **Actions → WRF-SMN (modelo 4 km) → Run workflow** para el primer ciclo. Tarda unos minutos. En el paso "Procesar ciclo nuevo" tiene que aparecer `Listo · 72 horas`.
3. Esperar la corrida siguiente de las alertas, o lanzar `seguimiento` a mano para ver el parte con el WRF.


## Versión 3 · noviembre 2026

### Nuevo: probabilidad y confianza de cada alerta
- Cada alerta por pronóstico de lluvia, viento, calor o frío lleva la probabilidad de que ocurra, calculada con los modelos de ensamble ECMWF, GFS e ICON (unos 120 escenarios), y un nivel de confianza: alta, media o baja.
- La perspectiva de los días 3 a 7 muestra la probabilidad de cada señal, y agrega las que ve el ensamble aunque el modelo principal no las muestre.
- Configuración nueva en `config.yaml`, sección `probabilidad`. Con `prob_minima_envio` se puede dejar de avisar por Telegram las alertas poco probables (está apagado: `0`).
- Cada aviso enviado se guarda en `historial.json`, para medir más adelante qué tan certero es el sistema.

### Correcciones
- **Mensajes que no llegan:** si Telegram falla, se reintenta hasta 4 veces. Si igual no llega, la alerta no se da por avisada y se vuelve a intentar en el ciclo siguiente. La corrida queda en rojo en Actions y GitHub manda un mail.
- **Estado:** se guarda en la rama `estado` del repositorio, no solo en la caché de Actions, que GitHub borra a los 7 días sin uso. Antes, si se perdía la caché, el bot repetía todas las alertas vigentes.
- **Modos manuales:** `perspectiva` y `seguimiento` ya se pueden elegir desde **Actions → Run workflow**. Ningún modo manual cambia el estado de las alertas.
- **Tablero sin datos:** los modos `prueba`, `simulacro`, `perspectiva` y `seguimiento` publicaban el tablero sin `alertas.json`. Ahora lo generan siempre.
- **Comandos viejos:** el bot ignora los comandos de más de 30 minutos, por ejemplo después de una interrupción.
- **Corrida en rojo sin motivo:** antes, si fallaba una sola fuente en un día sin alertas, la corrida terminaba con error y no se publicaba el tablero. Ahora solo se marca error si falla Telegram o fallan las dos fuentes, y siempre después de publicar.
- **Bot y tablero coinciden:** el tablero muestra las alertas que calcula el proceso, las mismas que manda el bot. Solo las calcula por su cuenta si el proceso no corre hace más de 90 minutos, y en ese caso lo avisa.
- **Granizo:** se desactivó (`granizo: false`). Open-Meteo no pronostica granizo para Sudamérica, así que esa condición nunca se cumplía.
- **Detalle por comuna:** los mensajes y el tablero aclaran que es orientativo.
- El workflow también se publica al cambiar `alertas.py`.

### Qué hay que hacer al subir esta versión
1. Reemplazar todos los archivos del repositorio por los de esta carpeta, incluido `.github/workflows/alertas.yml`.
2. **Settings → Actions → General → Workflow permissions:** verificar que esté en *Read and write permissions*, necesario para guardar el estado en la rama `estado`.
3. **Actions → Run workflow → `simulacro`** para ver el formato nuevo de los mensajes.
