# Estado actual del detector de tarjetas (as-built)

> Snapshot del 2026-11-19, actualizado al catálogo definitivo de **4 placas**.
> Describe cómo quedó el sistema después de los ajustes de campo. Las menciones a
> ids retirados que puedan aparecer en `BITACORA.md` son historia de la
> migración: el catálogo vigente es solo el de 4 fichas.
>
> **Supuestos de nomenclatura:** el requerimiento pedía “Arduino UNO R4”,
> “Arduino MKR WiFi” y “ESP32”; se concretaron como **UNO R4 WiFi**, **MKR WiFi
> 1010** y **ESP32 DevKit V1** (ESP32-WROOM-32).

## 1. Qué es y qué hace

Página web servida por la UNO Q en `http://<ip-de-la-placa>:8000` que detecta
placas del catálogo vigente (`arduino_uno_r4_wifi`, `raspberry_pi_4`,
`arduino_mkr_wifi_1010`, `esp32`) con **ORB + RANSAC** sobre el stream de una
cámara IP, y dibuja un **globo estabilizado** con la ficha técnica de la placa
reconocida. Interfaz de **entrenamiento** en `/teach`.

## 2. Cambios respecto al diseño original

### 2.1 Cámara: de webcam USB a cámara IP (URL editable desde la página)
- La webcam USB por el hub dejaba la placa inestable (alimentación justa → caídas de WiFi).
- Se pasó a **teléfono Android + app "IP Webcam"**. La IP del teléfono **cambia por DHCP**
  (fue `192.168.1.7` y luego `.8`), por eso la URL es editable sin tocar SSH.
- `app/config.py`: `CAMERA_URL` (env) + `CAMERA_URL_FILE` (`data/camera_url.txt`) +
  `load_camera_source()` con prioridad **archivo persistido → env → índice**.
- `app/camera.py`: `CameraCapture` acepta `source: int | str`; si es URL usa backend
  **FFMPEG** y NO aplica los `set()` V4L2 (FOURCC/ancho/alto/fps/buffersize); si es
  entero usa V4L2. `set_source()` reabre el stream en caliente.
- **Cambio desde la página**: barra "📷 Cámara" en `index.html` (input + botón "Aplicar")
  → `POST /api/camera` (valida `http://`/`https://`/`rtsp://`, sino 400) → persiste en
  `data/camera_url.txt` y reabre el stream. El input se autocompleta con la fuente activa
  (salvo que el usuario lo esté editando).

### 2.2 Paleta sobria (sin neón)
- `web/static/style.css`: `--bg #17171b`, `--panel #f3efe6`, `--green #c9a35a` (bronce),
  `--ink #191919`, `--danger #b4553f`, etc. Fondo oscuro + panel marfil + acento bronce.
- `web/static/app.js`: `COLOR_BOX #c9a35a`, `COLOR_BUBBLE #f3efe6`, `COLOR_INK #191919`.

### 2.3 Umbrales del detector (subidos para matar falsos positivos)
En `app/config.py`:

| Parámetro | Original | Actual |
|---|---|---|
| `SCORE_THRESHOLD` | 0.35 | **0.60** |
| `MIN_INLIERS` | 15 | **20** |
| `MAX_INSTANCES_PER_TEMPLATE` | 4 | **1** |

`app/detector.py` `_postprocess`: además del NMS por IoU, suprime por **contención**
(si el centro de una caja cae dentro de otra caja del mismo label, se descarta).

### 2.4 UI del globo (marco sobrio + estabilizado)
- `drawBox()` dibuja un **marco dorado fino con esquinas de visor** (`COLOR_BOX
  #c9a35a`, 5 px si la placa está seleccionada); ya no hay glow neón ni relleno.
- Globo **estabilizado** en `app.js`:
  - **Filtro de mediana** sobre una ventana de `SMOOTH_WINDOW=9` frames (rechaza el
    jitter frame-a-frame de ORB+RANSAC con la tarjeta quieta).
  - `SMOOTH_ALPHA=0.15` (EMA hacia la mediana), `DEADBAND=18` px (congela ante
    micro-movimiento), `FLIP_MARGIN=20` (histéresis arriba/abajo), `GAP=34` px
    (separación del globo a la tarjeta).
  - `state.smoothed` con `hist` (buffer de boxes), `lastSeen`; pruning con gracia de 1500 ms.
- `app/server.py`: estáticos con `cache=False` (no-store) para que el navegador no
  retenga JS/CSS viejos.

## 3. Estado de plantillas (`data/templates/`)

| Label | Muestras `.npz` | Estado |
|---|---|---|
| `arduino_uno_r4_wifi` | **0** | sin entrenar: hay que capturarla |
| `raspberry_pi_4` | **0** | sin entrenar: hay que capturarla |
| `arduino_mkr_wifi_1010` | **0** | sin entrenar: hay que capturarla |
| `esp32` | **0** | sin entrenar: hay que capturarla |

- `data/templates/` contiene **solo `.gitkeep`**: no hay ninguna plantilla `.npz`
  entrenada, ni del catálogo ni de etiquetas históricas.
- Las **plantillas sembradas desde imágenes de catálogo** se **borraron**
  (causaban falsos positivos de fondo) y no se versionan.
- `scripts/seed_images/` conserva fotos de placas retiradas (UNO R3, Nano,
  NodeMCU, Mega, shield, Raspberry Pi 3) como **material histórico**. No se cargan
  automáticamente, no generan plantillas y no forman parte del catálogo.
- `data/boards.json` tiene **4 fichas vigentes**, en este orden:
  `arduino_uno_r4_wifi`, `raspberry_pi_4`, `arduino_mkr_wifi_1010`, `esp32`.
- El test de degradación de `tests/test_api.py` usa la etiqueta **ficticia**
  `placa_legacy`; no existe ninguna carpeta con ese nombre en el repositorio.

## 4. Detección en vivo

- **Todavía no hay detección del catálogo en vivo**: con `data/templates/` vacío,
  `templates.count=0` y la UI muestra el banner “Todavía no hay plantillas”. El
  pipeline está listo; falta capturar las muestras.
- La degradación de etiquetas sin ficha está cubierta por tests con la etiqueta
  ficticia `placa_legacy`: la detección se publica con `name = label` y
  `summary = {}` sin romper la API.
- Umbrales vigentes para cuando se entrenen las placas: `SCORE_THRESHOLD=0.60`,
  `MIN_INLIERS=20`, `MAX_INSTANCES_PER_TEMPLATE=1`.
- El visor dibuja un **marco amarillo de área de escaneo** siempre visible y un
  **globo estabilizado** solo cuando hay detecciones.

## 5. Scripts auxiliares (`scripts/`)

| Script | Uso |
|---|---|
| `seed_templates.py` | genera plantillas ORB desde imágenes sueltas (mismas params que el detector) |
| `train_from_stream.py` | genera ~100-150 plantillas desde el stream de la cámara IP (video 1 min, con deduplicación) |
| `diag_match.py` | diagnóstico: carga un frame + una plantilla, reporta matches/inliers y su distribución |
| `deploy.sh` | despliegue SSH o USB (adb): renderiza la unidad desde plantilla, preserva `/etc/default/detector-tarjetas` y verifica el servicio |
| `discover_unoq.sh` | descubrimiento de la placa |

## 6. Pendientes

1. **Capturar y entrenar** las 4 placas del catálogo (`arduino_uno_r4_wifi`,
   `raspberry_pi_4`, `arduino_mkr_wifi_1010`, `esp32`) desde `/teach`, con la
   cámara y la luz definitivas. No hay ninguna plantilla `.npz` todavía.
2. Verificar en vivo `score`, `inliers` y estabilidad del globo para cada placa.
3. Ajustar `SCORE_THRESHOLD`/`MIN_INLIERS` solo si las muestras reales lo
   justifican (hoy 0.60 y 20).
4. Opcional: anclar el globo a posición fija de pantalla si el seguimiento molesta.
5. Registrar en `docs/BITACORA.md` los resultados de la campaña de captura.

## 7. Runbook operativo

- **Estado del despliegue**: servicio `detector-tarjetas` **enabled** y **active**
  con `Restart=always`; última vía de instalación USB/adb, serial `142970805`
  (ver `README.md` y `DESPLIEGUE_UNOQ.md`). El catálogo en la placa es el mismo de
  4 fichas; las plantillas siguen en 0 hasta entrenarlas.
- **Acceso**: `ssh -p 2222 -i ~/.ssh/unoq_key arduino@<ip-de-la-placa>` (sudo vía `sudo -S`);
  si la red no es alcanzable, USB-C + `adb -s 142970805 shell`.
- **Despliegue**: `scripts/deploy.sh --host <ip> --user arduino` o
  `scripts/deploy.sh --via-adb --adb-serial 142970805`. Renderiza la unidad desde
  `systemd/detector-tarjetas.service`, detecta la unidad instalada, siembra
  `/etc/default/detector-tarjetas` solo si no existe y verifica `is-enabled`/`is-active`/health.
- **Cambio de cámara**: sin SSH, desde la barra "📷 Cámara" de la página (ver 2.1).
- Detalles completos: ver `DESPLIEGUE_UNOQ.md`.
