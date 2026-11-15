# Estado actual del detector de tarjetas (as-built)

> Snapshot del 2026-11-14. Describe cómo quedó el sistema DESPUÉS de todos los
> ajustes de campo, que difieren del diseño original (`PLAN.md` / `README.md`).

## 1. Qué es y qué hace

Página web servida por la UNO Q en `http://192.168.1.5:8000` que detecta placas
(Arduino UNO/Nano/Mega, ESP32, NodeMCU, Raspberry Pi, shield Ethernet, etc.) con
**ORB + RANSAC** sobre el stream de una cámara IP, y dibuja un **globo cómic** con la
ficha técnica de la placa reconocida. Interfaz de **entrenamiento** en `/teach`.

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

### 2.4 UI del globo (sin recuadro + estabilizado)
- Se **quitó el recuadro** (no se llama `drawBox()`; solo queda el globo cómic).
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

| Label | Muestras | Origen |
|---|---|---|
| `raspberry_pi_2` | **24** | capturas reales del usuario (re-etiquetadas desde `raspberry_pi_4`) |
| resto | 0 | sin entrenar aún |

- Las **20 plantillas sembradas** desde imágenes de Wikimedia se **borraron** (causaban
  falsos positivos de fondo).
- `data/boards.json` tiene **9 fichas**: arduino_uno_r3, arduino_nano, raspberry_pi_4,
  raspberry_pi_5, esp32, nodemcu, raspberry_pi_2, arduino_mega_2560, arduino_ethernet_shield.

## 4. Detección en vivo

- Funciona: score **0.90–0.94** con la placa real Raspberry Pi 2.
- Falso positivo residual depende del **ángulo/fondo** (las 24 muestras incluyen el fondo
  de la mesa). Mejora: recapturar con fondo limpio o usar `train_from_stream.py`.

## 5. Scripts auxiliares (`scripts/`)

| Script | Uso |
|---|---|
| `seed_templates.py` | genera plantillas ORB desde imágenes sueltas (mismas params que el detector) |
| `train_from_stream.py` | genera ~100-150 plantillas desde el stream de la cámara IP (video 1 min, con deduplicación) |
| `diag_match.py` | diagnóstico: carga un frame + una plantilla, reporta matches/inliers y su distribución |
| `deploy.sh` | despliegue original |
| `discover_unoq.sh` | descubrimiento de la placa |

## 6. Pendientes

1. **Entrenar** las placas restantes (Mega, shield Ethernet, clon UNO) en `/teach`.
2. Recapturar Raspberry Pi 2 con **fondo limpio** para eliminar el falso positivo por fondo.
3. Confirmar estabilidad del globo (estático y separado de la tarjeta).
4. Opcional: anclar el globo a posición fija de pantalla si el seguimiento molesta.

## 7. Runbook operativo

- **Acceso**: `ssh -p 2222 -i ~/.ssh/unoq_key arduino@192.168.1.5` (sudo vía `sudo -S`).
- **Despliegue**: editar en notebook → `node --check` (si JS) / `py_compile` (si Python) →
  `scp` a `/home/arduino/` → `sudo install -m 0644 -o jma -g jma` a `/home/jma/detector-tarjetas/...` →
  `sudo systemctl restart detector-tarjetas` → esperar ~8-10 s.
- **Cambio de cámara**: sin SSH, desde la barra "📷 Cámara" de la página (ver 2.1).
- Detalles completos: ver `DESPLIEGUE_UNOQ.md`.
