# Documentación de desarrollador · Detector de tarjetas

**Versión:** 1.0.0
**Audiencia:** quien va a leer, modificar, extender o depurar el código.
**Requisito previo:** haber leído `README.md` y `docs/datasheet.md`.

---

## 1. Visión general de la arquitectura

La aplicación es un **monolito Python** sin frameworks: un servidor HTTP de la
biblioteca estándar, dos hilos de trabajo (cámara y detector) y un frontend
estático que consume JSON y un stream MJPEG.

```
┌───────────────────────────── Proceso Python (app.server) ─────────────────────────────┐
│                                                                                       │
│  main()                                                                               │
│   ├── DetectorTarjetasApp                                                             │
│   │    ├── BoardSpecs(data/boards.json)      caché por (mtime_ns, size)               │
│   │    ├── CameraCapture (Thread)      URL/FFMPEG o V4L2 → last_frame + queue(2)      │
│   │    ├── BoardDetector (Thread)            frame_queue → list[Detection]            │
│   │    └── TeachMode                         data/templates/<label>/*.npz             │
│   │                                                                                   │
│   └── DetectorHTTPServer (ThreadingHTTPServer)                                        │
│        └── Handler (uno por conexión)                                                 │
│             ├── GET  /, /teach, /static/*, /stream.mjpg                               │
│             ├── GET  /api/{detections,boards,health,teach/samples}                    │
│             └── POST /api/teach/{capture,reset,reload}, /api/camera                   │
└───────────────────────────────────────────────────────────────────────────────────────┘
```

Decisiones estructurales:

| Decisión | Motivo |
|---|---|
| `http.server.ThreadingHTTPServer` | Cero dependencias; el stream no bloquea la API |
| Hilos + colas en vez de asyncio | OpenCV y `VideoCapture.read()` son bloqueantes |
| Cajas normalizadas | El frontend no necesita conocer la resolución |
| Plantillas en disco (`.npz`) | Persistencia simple, recarga en caliente, respaldo trivial |
| Sin `pip` en la UNO Q | `python3-opencv` del sistema; menos superficie de fallo |
| Frontend sin build | Se sirve tal cual; no hay Node ni CDNs |

### 1.1 Mapa de módulos

| Archivo | Responsabilidad | Depende de |
|---|---|---|
| `app/config.py` | Constantes, rutas y variables de entorno | — |
| `app/camera.py` | Captura URL/FFMPEG o V4L2, reconexión, cambio en caliente, frame de estado | `config` |
| `app/detector.py` | ORB, matching, homografía, NMS, serialización NPZ | `config` |
| `app/specs.py` | Carga/validación de `boards.json`, formateo | `config` |
| `app/teach.py` | Captura/borrado de muestras | `config`, `detector` |
| `app/server.py` | HTTP, rutas, stream, composición, `main()` | todos |
| `app/__init__.py` | `__version__ = "1.0.0"` | — |

### 1.2 Flujo de datos

```
URL/FFMPEG o V4L2 ──► CameraCapture._publish(frame)
           ├── self._last_frame = frame        (stream MJPEG)
           └── frame_queue.put_nowait(frame)   (detector; reemplaza el viejo)

BoardDetector.run()
  frame = frame_queue.get(timeout=0.2)
  detections = detect_frame(frame)
  self._detections = detections             (bajo lock)
  self._last_inference_ms = ...

GET /api/detections
  detector.get_detections() + specs.get_summary(label) ──► JSON

Navegador
  fetch cada 250 ms ──► dibuja cajas y globos en <canvas>
```

---

## 2. Hilos, locks y colas

### 2.1 Hilos

| Hilo | Clase | Daemon | Ciclo |
|---|---|---|---|
| `camera-capture` | `CameraCapture` | Sí | Abrir cámara → leer frame → publicar → repetir |
| `board-detector` | `BoardDetector` | Sí | `get` de la cola → inferir → publicar → esperar intervalo |
| HTTP (N) | `Handler` | Sí (`daemon_threads=True`) | Una conexión por hilo |
| `shutdown` | `threading.Thread` | Sí | Ejecuta `server.shutdown()` al recibir señal |

`DetectorTarjetasApp.start()` arranca cámara y detector; `stop()` detiene primero
el detector y después la cámara. Los hilos son daemon para que un cierre
inesperado del proceso no quede colgado.

### 2.2 Cola de frames

```python
frame_queue = queue.Queue(maxsize=config.FRAME_QUEUE_SIZE)  # 2
```

- El productor (`CameraCapture._publish`) usa `put_nowait`. Si la cola está
  llena, **descarta el frame más viejo** (`get_nowait`) y vuelve a intentar. Es
  una política de “último frame gana”: al detector no le sirve procesar imágenes
  atrasadas.
- El consumidor (`BoardDetector.run`) usa `get(timeout=0.2)` para poder revisar
  el `_stop_event` aunque no lleguen frames.
- Un `None` en la cola se ignora (no se usa como centinela; el cese se señaliza
  con `_stop_event`).

### 2.3 Locks

| Lock | Protege | Notas |
|---|---|---|
| `CameraCapture._lock` | `_capture`, `_last_frame`, `_is_open`, `_resolution`, `_fps`, `_frame_times`, `_open_attempts` | Se libera antes de `cap.release()` para no bloquear el hilo de captura |
| `BoardDetector._templates_lock` | `_templates` (dict label → list[Template]) | `_snapshot_templates()` copia la lista plana y libera el lock antes de inferir |
| `BoardDetector._detections_lock` | `_detections`, `_last_inference_ms`, `_inference_count` | Lecturas devuelven copias |
| `TeachMode._lock` | Escritura y borrado de archivos `.npz` | Serializa operaciones de disco |
| `BoardSpecs._lock` | Caché de fichas y clave `(mtime_ns, size)` | Recarga automática al cambiar el archivo |

Regla de oro: **nunca se mantiene un lock mientras se ejecuta OpenCV**. La
inferencia trabaja sobre una copia (`_snapshot_templates`) y sobre el frame
recibido; los locks solo cubren el intercambio de referencias.

### 2.4 Cierre ordenado

```
SIGINT/SIGTERM
  └── _shutdown() lanza un hilo que llama server.shutdown()
        └── serve_forever() retorna
              └── finally: server.server_close(); app.stop()
                    ├── detector.stop(): _stop_event.set(); join(2 s)
                    └── camera.stop(): _stop_event.set(); join(3 s);
                        si sigue vivo, libera VideoCapture y join(2 s)
```

`CameraCapture.stop()` libera el `VideoCapture` **desde afuera** solo si el hilo
quedó bloqueado en `read()`; hacerlo en paralelo con una lectura puede abortar el
backend V4L2, por eso primero se intenta el `join` amable.

---

## 3. Módulo `app/config.py`

Todas las constantes y rutas. Las variables de entorno se leen **al importar**:

| Constante | Origen / valor | Tipo |
|---|---|---|
| `BASE_DIR` | `Path(__file__).resolve().parent.parent` | `Path` |
| `DATA_DIR` | `BASE_DIR / "data"` | `Path` |
| `BOARDS_JSON` | `BOARDS_JSON` o `DATA_DIR/boards.json` | `Path` |
| `TEMPLATE_DIR` | `TEMPLATE_DIR` o `DATA_DIR/templates` | `Path` |
| `WEB_DIR` | `BASE_DIR / "web"` | `Path` |
| `STATIC_DIR` | `WEB_DIR / "static"` | `Path` |
| `CAMERA_INDEX` | `CAMERA_INDEX` o `0` | `int` |
| `CAMERA_URL` | `CAMERA_URL` o `""` | `str` |
| `CAMERA_URL_FILE` | `CAMERA_URL_FILE` o `DATA_DIR/camera_url.txt` | `Path` |
| `CAMERA_WIDTH` / `CAMERA_HEIGHT` | `640` / `480` (fallback/negociación; la calibración de `DEFAULT_SCAN_QUAD` asume frame portrait **480×640**) | `int` |
| `CAMERA_FPS_TARGET` | `15` | `int` |
| `CAMERA_RETRY_DELAY_S` | `2.0` | `float` |
| `FRAME_QUEUE_SIZE` | `2` | `int` |
| `DETECTOR_INTERVAL_MS` | `250` | `int` |
| `ORB_FEATURES` | `ORB_FEATURES` o `500` | `int` |
| `ORB_EDGE_THRESHOLD` | `31` | `int` |
| `ORB_PATCH_SIZE` | `31` | `int` |
| `LOWE_RATIO` | `0.75` | `float` |
| `MIN_MATCH_COUNT` | `10` | `int` |
| `MIN_INLIERS` | `20` | `int` |
| `RANSAC_REPROJ_THRESHOLD` | `5.0` | `float` |
| `SCORE_THRESHOLD` | `0.60` | `float` |
| `MIN_BOX_AREA_RATIO` | `0.02` | `float` |
| `MAX_BOX_AREA_RATIO` | `0.90` | `float` |
| `MIN_VISIBLE_QUAD_RATIO` | `0.80` (fracción mínima del quad proyectado dentro del cuadrilátero de escaneo) | `float` |
| `SCAN_QUAD` / `DEFAULT_SCAN_QUAD` | 4 puntos normalizados TL, TR, BR, BL; default `(0.1762,0.2962)`, `(0.6477,0.2962)`, `(0.6477,0.6499)`, `(0.1762,0.6499)`: cuadrado de 226×226 px centrado en las T, calibrado para frame portrait **480×640** | `tuple[tuple[float, float], ...]` |
| `NMS_IOU_THRESHOLD` | `0.30` | `float` |
| `INSTANCE_CENTER_DISTANCE_PX` | `40.0` | `float` |
| `MAX_INSTANCES_PER_TEMPLATE` | `1` (una placa por plantilla; más instancias generaban cajas fantasma) | `int` |
| `MIN_POLYGON_FILL_RATIO` | `0.20` | `float` |
| `TEMPLATE_MIN_KEYPOINTS` | `50` | `int` |
| `TEMPLATE_EXTENSION` | `".npz"` | `str` |
| `DEFAULT_LABELS` | 4 etiquetas de fábrica | `tuple[str, ...]` |
| `SERVER_HOST` | `HOST` o `"0.0.0.0"` | `str` |
| `SERVER_PORT` | `PORT` o `8000` | `int` |
| `JPEG_QUALITY` | `85` | `int` |
| `STREAM_BOUNDARY` | `b"--frame"` | `bytes` |
| `STREAM_CONTENT_TYPE` | `multipart/x-mixed-replace; boundary=frame` | `str` |
| `STREAM_MAX_FPS` | `15` | `int` |
| `MAX_JSON_BODY_BYTES` | `64 * 1024` | `int` |
| `CORS_ALLOW_ORIGIN` | `"*"` | `str` |

> `DETECTOR_INTERVAL_MS` y `TEMPLATE_MIN_KEYPOINTS` se usan como **default de
> argumento** en los constructores; para cambiarlos en runtime hay que pasarlos
> explícitamente (`DetectorTarjetasApp(interval_ms=...)`,
> `TeachMode(min_keypoints=...)`).

### `load_camera_source() -> int | str`

Resuelve la fuente de video al arrancar, con esta precedencia:

1. `data/camera_url.txt` (URL persistida desde la barra “📷 Cámara”).
2. Variable de entorno `CAMERA_URL`.
3. Variable de entorno `CAMERA_INDEX` (índice V4L2).

El archivo persistido gana sobre el entorno para que un cambio hecho desde el
navegador sobreviva a un reinicio del servicio sin editar
`/etc/default/detector-tarjetas`.

---

## 4. Módulo `app/detector.py`

### 4.1 Utilidades de serialización

#### `normalize_label(raw, fallback="") -> str`

Limpia un label proveniente de un `.npz`:

1. Si es `np.ndarray` 0-d, usa `.item()`; si es lista, toma el primer elemento o
   concatena.
2. Si es `bytes`, decodifica UTF-8 con `errors="replace"`.
3. Convierte a `str`, hace `strip()` y elimina comillas simples o dobles
   envolventes.
4. Devuelve `fallback` si queda vacío.

Motivo histórico (I-01): `np.savez` guarda un array 0-d de Unicode y `str(array)`
devuelve `"'arduino_uno_r4_wifi'"`. Sin esta función, el label no coincidía con los
IDs de `boards.json`.

#### `create_orb(...) -> cv2.ORB`

Crea `cv2.ORB_create(nfeatures, edgeThreshold, patchSize, fastThreshold=20,
scoreType=cv2.ORB_HARRIS_SCORE)`.

#### `save_template_archive(path, keypoints, descriptors, image_size, label) -> Path`

Crea los directorios padres, normaliza el label y guarda con
`np.savez_compressed`:

| Clave | dtype | Forma |
|---|---|---|
| `label` | Unicode | 0-d |
| `descriptors` | `uint8` | `(N, 32)` |
| `points` | `float32` | `(N, 2)` |
| `sizes` | `float32` | `(N,)` |
| `angles` | `float32` | `(N,)` |
| `responses` | `float32` | `(N,)` |
| `octaves` | `int32` | `(N,)` |
| `class_ids` | `int32` | `(N,)` (`-1` si `None`) |
| `image_size` | `int32` | `(2,)` = `(ancho, alto)` |

Si `keypoints` está vacío, escribe arrays vacíos con la forma correcta.

#### `deserialize_keypoints(data) -> list[cv2.KeyPoint]`

Reconstruye `cv2.KeyPoint(x, y, size, angle, response, octave, class_id)` a
partir de los arrays del NPZ.

### 4.2 Modelos de datos

```python
@dataclass
class Template:
    label: str
    path: Path
    points: np.ndarray        # (N, 2) float32, coords en la imagen origen
    descriptors: np.ndarray   # (N, 32) uint8
    image_size: tuple[int, int]
    keypoints: list[cv2.KeyPoint] = field(default_factory=list)

@dataclass
class Detection:
    label: str
    score: float
    box: Box                  # (x, y, w, h) normalizado [0,1]
    instance_id: int = 0
    center: tuple[float, float] = (0.5, 0.5)  # normalizado
    inliers: int = 0
```

`Detection.to_dict()` devuelve `label`, `score` (3 decimales), `box` (4
decimales), `instance_id` e `inliers`.

### 4.3 `box_iou(a, b) -> float`

IoU de dos cajas normalizadas. Devuelve `0.0` si no hay intersección o la unión
es cero.

### 4.4 `BoardDetector`

Hilo de inferencia.

```python
BoardDetector(
    frame_queue: queue.Queue[np.ndarray],
    template_dir: Optional[Path] = None,
    interval_ms: int = config.DETECTOR_INTERVAL_MS,
    matcher: Optional[cv2.BFMatcher] = None,
)
```

Propiedades: `last_inference_ms`, `inference_count`, `template_labels`,
`template_counts`.

Métodos públicos:

| Método | Descripción |
|---|---|
| `reload_templates() -> dict[str, int]` | Reescanea `template_dir` y reemplaza el mapa bajo lock |
| `get_detections() -> list[Detection]` | Copia de la última lista publicada |
| `detect_frame(frame) -> list[Detection]` | Inferencia pura (sin cola), usada por tests |
| `stop()` | Señala parada y hace `join(2 s)` |
| `run()` | Bucle del hilo |

Métodos internos:

| Método | Descripción |
|---|---|
| `_load_template(path, label)` | Abre el NPZ con `allow_pickle=False` y valida formas |
| `_snapshot_templates()` | Lista plana de plantillas bajo lock |
| `_match_template(...)` | Matching multi-instancia de una plantilla |
| `_build_detection(...)` | Proyección, validaciones y score |
| `_postprocess(...)` | NMS, agrupación y `instance_id` |

#### Carga y validación de plantillas

`reload_templates()` recorre `template_dir/*/*.npz` ordenado. Por cada archivo:

- captura `OSError`, `ValueError` y `KeyError` → log de advertencia y sigue;
- valida `descriptors.ndim == 2` y `shape[1] == 32`;
- valida `len(points) == len(descriptors)`;
- valida `len(image_size) == 2`.

El label efectivo es el **guardado dentro del NPZ** (`normalize_label`), con el
nombre del directorio como fallback. Consecuencia: si renombrás la carpeta pero no
el contenido, la detección sigue usando el label guardado. Para evitar sorpresas,
mantené carpeta y label sincronizados.

#### `detect_frame(frame)`

```
1. snapshot de plantillas; si no hay, return []
2. gray = BGR2GRAY si ndim == 3
3. si height < 32 o width < 32 → []
4. keypoints, descriptors = ORB.detectAndCompute(gray)
5. si descriptors is None o len < MIN_MATCH_COUNT → []
6. frame_points = float32([kp.pt])
7. por cada plantilla: _match_template(...)
8. _postprocess(candidatos)
```

#### `_match_template(template, frame_descriptors, frame_points, frame_size)`

Pseudocódigo:

```
available = [0..len(frame_descriptors)-1]
para intento en range(MAX_INSTANCES_PER_TEMPLATE):
    si len(available) < MIN_MATCH_COUNT: break
    subset = frame_descriptors[available]
    knn = matcher.knnMatch(template.descriptors, subset, k=2)
    good = [m for m,n in knn if m.distance < LOWE_RATIO * n.distance]
    si len(good) < MIN_MATCH_COUNT: break
    src = template.points[m.queryIdx]          # query = plantilla
    dst = frame_points[available[m.trainIdx]]  # train  = frame
    H, mask = findHomography(src, dst, RANSAC, 5.0)
    si H is None o inliers < MIN_INLIERS: break
    det = _build_detection(...)
    si det: results.append(det)
    consumidos = keypoints del frame dentro de la caja + 8 px
                 (o solo los inliers si la detección fue rechazada)
    si no hay consumidos: break
    available -= consumidos
```

Detalles importantes:

- `knnMatch` se llama con la **plantilla como query** y el frame como train. Por
  eso `queryIdx` indexa `template.points` y `trainIdx` indexa el subconjunto del
  frame.
- Con `MAX_INSTANCES_PER_TEMPLATE = 1` el bucle hace **un solo intento**: el
  visor muestra una placa por etiqueta y las copias adicionales se descartan.
  El consumo por caja (no solo inliers) y el margen de 8 px siguen
  implementados: si se sube la constante, permiten que el ratio test vuelva a
  discriminar una segunda copia sin reutilizar keypoints de la primera. Ese modo
  estuvo activo y generaba cajas fantasma, por eso quedó en 1.

#### `_build_detection(...)`

```
inliers = src[inlier_mask]                 # puntos de la plantilla
x0,y0 = min; x1,y1 = max
pad = max(4, 0.02 * max(ancho, alto))
clamp a [0, image_size]
corners = [(x0,y0),(x1,y0),(x1,y1),(x0,y1)]
projected = perspectiveTransform(corners, H)
si no finito → None
quad_area = |contourArea(projected)|
visible_area = intersectConvexConvex(projected, cuadrilátero SCAN_QUAD)
si quad_area <= 0 o visible_area/quad_area < MIN_VISIBLE_QUAD_RATIO → None  # fuera del área
polygon_area = quad_area
bx,by,bw,bh = boundingRect(projected)
si bw <= 1 o bh <= 1 → None
si polygon_area < MIN_POLYGON_FILL_RATIO * bw * bh → None   # homografía degenerada
clamp caja al frame; si < 8x8 px → None
area_ratio = área_caja / área_frame
si fuera de [MIN_BOX_AREA_RATIO, MAX_BOX_AREA_RATIO] → None
score = 0.6*inlier_ratio + 0.3*distance_score + 0.1*area_score
si score < SCORE_THRESHOLD → None
box normalizada; center normalizado
```

Fórmulas exactas:

```
inlier_ratio   = inliers / len(good)
distance_score = clamp(1 - mean(distances de inliers)/90, 0, 1)
area_score     = min(1, (area_ratio / MIN_BOX_AREA_RATIO) * 0.5)
score          = 0.6*inlier_ratio + 0.3*distance_score + 0.1*area_score
```

> **Área de escaneo y filtro de visibilidad (ciclo 2).** `SCAN_QUAD` define el
> cuadrilátero donde se apoya la tarjeta, con los cuatro puntos normalizados en
> orden TL, TR, BR, BL. Por defecto son las puntas internas de las marcas en T
> de la hoja de calibración; se puede sobrescribir con
> `SCAN_QUAD="x1,y1,x2,y2,x3,y3,x4,y4"`. `detect_frame` recorta el frame al
> bounding box del quad para ORB y luego descarta los keypoints que no caen
> dentro del polígono (`points_in_scan_quad`); las coordenadas se mantienen en
> el sistema full-frame. `_match_template` aplica el mismo filtro a los
> keypoints de cada plantilla y, si quedan menos de `MIN_MATCH_COUNT`, la salta.
> Antes de calcular área/score se exige que al menos
> `MIN_VISIBLE_QUAD_RATIO` (0.80) del área del quad proyectado caiga dentro del
> cuadrilátero (`cv2.intersectConvexConvex`). Esto descarta los falsos positivos
> de fondo: las plantillas capturadas a frame completo incluyen la hoja/brazo
> estático y la homografía puede proyectar el quad sobre las T o fuera del
> área aunque la placa no esté presente. El resto del scoring no cambió.

#### `_postprocess(candidates, frame_size)`

1. Ordena por score descendente.
2. **NMS**: conserva una detección solo si su IoU con todas las ya conservadas es
   `<= NMS_IOU_THRESHOLD` (0.30).
3. **Supresión por contención**: descarta una detección si el centro de una caja
   de la **misma etiqueta** cae dentro de otra caja ya conservada. Esto elimina
   la caja grande que a veces envuelve a la placa real.
4. **Merge**: para detecciones de la misma etiqueta con centros a menos de
   `INSTANCE_CENTER_DISTANCE_PX` (40 px, calculado con `width`/`height`),
   conserva la de mayor score.
5. **Agrupa por label**, ordena cada grupo por `(center.x, center.y)` y asigna
   `instance_id = 0, 1, 2…`.
6. Ordena la salida final por score descendente.

---

## 5. Formato de plantillas `.npz`

### 5.1 Estructura

Un archivo por **muestra** (no por placa). Una placa puede tener N archivos:

```
data/templates/
├── arduino_uno_r4_wifi/
│   ├── 20261113-101502-123-a1b2c3.npz
│   └── 20261113-101510-456-d4e5f6.npz
├── raspberry_pi_4/
├── arduino_mkr_wifi_1010/
└── esp32/
```

Nombre generado por `TeachMode.capture`:

```
<YYYYmmdd-HHMMSS>-<milisegundos 000-999>-<uuid hex 6>.npz
```

> **Etiquetas fuera del catálogo**: el detector escanea todas las carpetas de
> `data/templates/` aunque el label no exista en `data/boards.json`. Esas
> etiquetas se cargan igual y se muestran sin ficha técnica. El caso de prueba es
> la etiqueta **ficticia** `placa_legacy`, que usa `tests/test_api.py` para
> verificar la degradación; no hay muestras reales en el repositorio porque
> `data/templates/` solo contiene `.gitkeep`. `/api/boards` solo lista el
> catálogo de 4 placas.

### 5.2 Inspección

```python
import numpy as np

path = "data/templates/arduino_uno_r4_wifi/20261113-101502-123-a1b2c3.npz"
with np.load(path, allow_pickle=False) as data:
    for key in data.files:
        print(f"{key:12s} {data[key].dtype} {data[key].shape}")
    print("label:", data["label"].item())
    print("image_size:", data["image_size"].tolist())
```

Salida típica:

```
label        <U19  ()
descriptors  uint8 (312, 32)
points       float32 (312, 2)
sizes        float32 (312,)
angles       float32 (312,)
responses    float32 (312,)
octaves      int32 (312,)
class_ids    int32 (312,)
image_size   int32 (2,)
```

### 5.3 Reglas de compatibilidad

- **Nunca** usar `pickle` (`allow_pickle=False` es deliberado).
- `descriptors` debe ser `(N, 32) uint8`; otra forma se rechaza al cargar.
- `points` y `descriptors` deben tener la misma longitud.
- `image_size` es `(ancho, alto)`.
- El label debe cumplir `^[a-z0-9_]{2,40}$` para poder capturarse desde la UI.
- Archivos corruptos no tumban el servidor: se omiten con un warning.

---

## 6. Módulo `app/camera.py`

### 6.1 `CameraCapture`

```python
CameraCapture(
    source=config.load_camera_source(),  # int (índice) o str (URL)
    width=config.CAMERA_WIDTH,
    height=config.CAMERA_HEIGHT,
    fps_target=config.CAMERA_FPS_TARGET,
    retry_delay=config.CAMERA_RETRY_DELAY_S,
    frame_queue=None,
)
```

Propiedades: `source` (fuente efectiva), `index` (alias de `source`, se conserva
por compatibilidad), `is_open`, `resolution`, `fps`, `last_frame`,
`open_attempts`.

| Método | Comportamiento |
|---|---|
| `get_frame()` | Devuelve `_last_frame` bajo lock, sin bloquear |
| `set_source(source)` | Cambia `int`/`str`, resetea intentos y fuerza reapertura |
| `stop()` | `join(3 s)`; si sigue vivo, libera la captura y `join(2 s)` |
| `run()` | Bucle de apertura/lectura/publicación |
| `_try_open()` | URL → `CAP_FFMPEG`; índice → `CAP_V4L2` y fallback `CAP_ANY` |
| `_release_capture()` | Libera el `VideoCapture` y marca `is_open=False` |
| `_publish(frame)` | Actualiza `last_frame`, resolución, FPS y encola para el detector |
| `make_status_frame(message)` | Genera el frame gris con texto |

### 6.2 Apertura

Para una **URL** (`str`, típicamente `http://<telefono>:8080/video` de IP
Webcam) se abre con backend `CAP_FFMPEG` y **no** se aplican propiedades V4L2:

```python
cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
ok, frame = cap.read()
```

Para un **índice** (`int`) se prueba `CAP_V4L2` y luego `CAP_ANY`, aplicando
formato MJPG y resolución:

```python
cap = cv2.VideoCapture(index, backend)
cap.set(CAP_PROP_FOURCC, fourcc("MJPG"))
cap.set(CAP_PROP_FRAME_WIDTH, width)
cap.set(CAP_PROP_FRAME_HEIGHT, height)
cap.set(CAP_PROP_FPS, fps_target)
cap.set(CAP_PROP_BUFFERSIZE, 1)
ok, frame = cap.read()
```

Se valida con una lectura real; si falla, se libera y se prueba el siguiente
backend. Al abrir, se publica el primer frame y se registra el backend usado
(`FFMPEG`, `V4L2` o `default`) en el log.

La fuente efectiva se muestra en `/api/health` como `camera.index`, y
`POST /api/camera` la cambia en caliente sin reiniciar el servicio.

### 6.3 FPS

Media móvil sobre los últimos **30** timestamps (`deque(maxlen=30)`):

```
fps = (len(frame_times) - 1) / (último - primero)
```

Se redondea a 1 decimal en la propiedad.

### 6.4 Frame de estado

`make_status_frame()` dibuja sobre fondo gris (42) dos diagonales, un rectángulo
y tres líneas centradas:

```
CAMARA NO DISPONIBLE        (verde)
device 0 - reintentando cada 2s
Revisa el cable USB o el indice CAMERA_INDEX
```

No usa acentos porque `cv2.putText` con fuentes Hershey no los representa. El
frame también se encola para el detector (no tiene esquinas, así que no produce
detecciones).

---

## 7. Módulo `app/specs.py`

### 7.1 `BoardSpec`

Dataclass `frozen` con:

```python
id, name, short_name, mcu_soc, clock_mhz, ram_mb, flash_storage_gb,
gpio_count, voltage_v, connectivity: tuple[str,...], dimensions_mm: tuple[float,...],
year, description, image_path: Optional[str], urls: tuple[str,...],
extra: dict[str, Any]
```

Métodos:

- `to_dict()`: serializa todos los campos y agrega `extra` con `setdefault` (no
  pisa claves conocidas).
- `formatted()`: valores listos para la UI:
  `mcu_soc`, `clock`, `ram`, `storage`, `gpio`, `voltage`, `dimensions`,
  `connectivity`, `year`.

### 7.2 Formateadores

| Función | Ejemplos |
|---|---|
| `format_ram(0.002)` | `"2 KB"` |
| `format_ram(512)` | `"512 MB"` |
| `format_ram(2048)` | `"2 GB"` |
| `format_storage(0)` | `"microSD"` |
| `format_storage(0.00003...)` | `"32 KB"` |
| `format_storage(0.5)` | `"512 MB"` |
| `format_clock(16)` | `"16 MHz"` |
| `format_clock(2400)` | `"2.4 GHz"` |
| `format_dimensions((85, 56))` | `"85 × 56 mm"` |

### 7.3 `BoardSpecs`

```python
BoardSpecs(path=None)   # default config.BOARDS_JSON
```

| Método | Descripción |
|---|---|
| `get_all()` | Lista de fichas válidas |
| `get_by_id(id)` | Ficha o `None` |
| `get_summary(id)` | `{"MCU": ..., "RAM": ..., "Reloj": ...}` o `{}` |
| `ids()` | Lista de IDs |

`get_summary` toma el MCU cortando en el primer `(`:
`"Renesas RA4M1 (Cortex-M4, 32 bits) @48 MHz + módulo ESP32-S3 (Wi-Fi/BLE, 8 MB flash)"`
→ `"Renesas RA4M1"`.

Caché: clave `(st_mtime_ns, st_size)`. Si el archivo cambia, se relee y se
revalida. Si no existe → `FileNotFoundError`; JSON inválido → `ValueError` con
línea/columna; campos faltantes → `ValueError` con la lista; IDs duplicados →
`ValueError`.

### 7.4 Esquema de `data/boards.json`

Lista no vacía de objetos. Campos obligatorios:

| Campo | Tipo | Ejemplo |
|---|---|---|
| `id` | string no vacío | `"arduino_uno_r4_wifi"` |
| `name` | string | `"Arduino UNO R4 WiFi"` |
| `short_name` | string | `"UNO R4 WiFi"` |
| `mcu_soc` | string | `"Renesas RA4M1 (Cortex-M4, 32 bits) @48 MHz + módulo ESP32-S3 (Wi-Fi/BLE, 8 MB flash)"` |
| `clock_mhz` | número | `48` |
| `ram_mb` | número | `0.03125` |
| `flash_storage_gb` | número | `0.000244140625` |
| `gpio_count` | entero | `20` |
| `voltage_v` | número | `5` |
| `connectivity` | lista de strings | `["USB-C","Wi-Fi 802.11 b/g/n 2.4 GHz","Bluetooth 5 LE"]` |
| `dimensions_mm` | lista de ≥2 números | `[68.85, 53.34]` |
| `year` | entero | `2023` |
| `description` | string | texto largo |

Opcionales: `image_path` (string o `null`), `urls` (lista de strings). Cualquier
otra clave se conserva en `extra` y se publica en `/api/boards`.

Ejemplo con una ficha real del catálogo (tomado de `data/boards.json`):

```json
[
  {
    "id": "esp32",
    "name": "ESP32 DevKit V1",
    "short_name": "ESP32",
    "mcu_soc": "Espressif ESP32-WROOM-32 (Xtensa LX6, 2 núcleos)",
    "clock_mhz": 240,
    "ram_mb": 0.52,
    "flash_storage_gb": 0.004,
    "gpio_count": 25,
    "voltage_v": 3.3,
    "connectivity": ["Wi-Fi 2.4 GHz", "Bluetooth 4.2", "UART", "I2C", "SPI", "ADC"],
    "dimensions_mm": [51.5, 28.5],
    "year": 2016,
    "description": "Placa de desarrollo basada en el ESP32-WROOM-32 de Espressif.",
    "urls": ["https://www.espressif.com/en/products/socs/esp32"]
  }
]
```

---

## 8. Módulo `app/teach.py`

### 8.1 `TeachMode`

```python
TeachMode(template_dir=None, min_keypoints=config.TEMPLATE_MIN_KEYPOINTS)
```

Crea el directorio si no existe y un ORB propio.

### 8.2 `validate_label(label) -> str`

- Exige `str`; hace `strip().lower()`.
- Valida `LABEL_PATTERN = ^[a-z0-9_]{2,40}$`.
- Lanza `ValueError` con mensaje en español si no cumple.

Esta validación es la barrera contra *path traversal* en los endpoints de teach.

### 8.3 `capture(label, frame, camera_open=True) -> dict`

Orden de rechazos:

1. Label inválido → `{ok: False, message, keypoints: 0, samples: N}`.
2. `camera_open=False` → rechazo explícito (no se guarda el frame sintético).
3. `frame is None` → rechazo.
4. ORB encuentra menos de `min_keypoints` (50) → rechazo con conteo.
5. Error de disco (`OSError`) → rechazo con detalle.

Si todo va bien:

- calcula grises y ORB;
- genera nombre único;
- guarda con `save_template_archive` bajo `_lock`;
- devuelve `{ok: True, message, keypoints, samples, file}`.

La captura **no** recorta la placa: guarda la imagen completa. El detector
proyecta el área de inliers, así que el fondo también forma parte de la
plantilla. Por eso se recomienda un fondo limpio.

### 8.4 `reset(label) -> dict`

- `label == "__all__"` → borra todos los `*.npz` de todos los subdirectorios.
- Otro label → valida y borra solo su carpeta.
- Devuelve `{ok, message, removed}`.

### 8.5 Conteos

| Método | Resultado |
|---|---|
| `list_labels()` | Etiquetas con al menos un `.npz` |
| `count_samples(label)` | Cantidad para una etiqueta (0 si inválida) |
| `counts()` | `{label: n}` con las 4 `DEFAULT_LABELS` incluidas en 0 |

---

## 9. Módulo `app/server.py`

### 9.1 `encode_jpeg(frame, quality=85) -> bytes | None`

`cv2.imencode(".jpg", frame, [IMWRITE_JPEG_QUALITY, quality])`. Devuelve `None`
si OpenCV falla o no puede codificar.

### 9.2 `DetectorHTTPServer`

`ThreadingHTTPServer` con:

```python
daemon_threads = True
allow_reuse_address = True
request_queue_size = 32
```

### 9.3 `DetectorTarjetasApp`

Composición e inyección de dependencias:

```python
DetectorTarjetasApp(
    boards_path=None,
    template_dir=None,
    camera_index=None,
    camera=None,          # inyectable para tests (FakeCamera)
    interval_ms=config.DETECTOR_INTERVAL_MS,
)
```

| Método | Descripción |
|---|---|
| `start()` | `detector.reload_templates()`, `camera.start()`, `detector.start()` |
| `stop()` | `detector.stop()` y luego `camera.stop()` |
| `boards_payload()` | Fichas + `formatted` + `summary` |
| `detection_payload()` | Detecciones + `name`/`short_name`/`summary` (degrada si el label no está en `boards.json`) |
| `health_payload()` | Estado completo, con `camera.index` = fuente efectiva |
| `set_camera_source(url)` | Persiste la URL en `CAMERA_URL_FILE` y llama a `camera.set_source()` |
| `build_server(host, port)` | Crea `DetectorHTTPServer` con el handler asociado |

La cámara inyectada debe exponer: `frame_queue`, `is_open`, `resolution`,
`fps`, `index`, `start()`, `stop()`, `get_frame()`, `make_status_frame()` y,
para el cambio en caliente, `set_source()`.

`detection_payload()` busca cada label en `BoardSpecs`. Si existe, agrega
`name`, `short_name` y `summary`; si no existe (por ejemplo, la etiqueta
ficticia `placa_legacy` usada por los tests), devuelve
`name = short_name = label` y `summary = {}` sin romper la respuesta. La UI
dibuja el label y el panel avisa que no hay ficha.

### 9.4 `make_handler(app)`

Crea una clase `Handler(BaseHTTPRequestHandler)` con `app` como atributo de
clase. `protocol_version = "HTTP/1.1"` y `server_version = "DetectorTarjetas/1.0.0"`.

Helpers:

| Helper | Efecto |
|---|---|
| `_add_cors()` | `Access-Control-Allow-Origin/Methods/Headers` |
| `_send_json(payload, status=200)` | JSON UTF-8, `no-store`, CORS |
| `_send_error_json(status, message, detail="")` | `{ok: false, error, detail?}` |
| `_send_bytes(body, content_type, status=200, cache=False)` | Archivos; `max-age=3600` si `cache` |
| `_read_json_body()` | Exige `Content-Length` 1..64 KiB, parsea objeto JSON |

### 9.5 Rutas

| Método | Ruta | Handler | Respuesta |
|---|---|---|---|
| GET | `/`, `/index.html` | `_serve_page("index.html")` | HTML |
| GET | `/teach`, `/teach/`, `/teach.html` | `_serve_page("teach.html")` | HTML |
| GET | `/static/<rel>` | `_serve_static` | Archivo con MIME y caché |
| GET | `/stream.mjpg` | `_serve_stream` | MJPEG |
| GET | `/api/detections` | `detection_payload()` | JSON |
| GET | `/api/boards` | `boards_payload()` | JSON |
| GET | `/api/health` | `health_payload()` | JSON |
| GET | `/api/teach/samples` | `{"counts": teach.counts()}` | JSON |
| GET | `/favicon.ico` | 204 sin cuerpo | — |
| POST | `/api/teach/capture` | `_handle_capture` | 200 / 422 / 400 |
| POST | `/api/teach/reset` | `_handle_reset` | 200 / 400 |
| POST | `/api/teach/reload` | `_handle_reload` | 200 / 400 |
| POST | `/api/camera` | `_handle_set_camera` | 200 / 400 / 500 |
| OPTIONS | cualquiera | 204 + CORS | — |
| otro | — | 404 JSON | — |

`do_GET` y `do_POST` envuelven todo en `try/except` y devuelven 500 JSON ante
errores no controlados; `BrokenPipeError` y `ConnectionResetError` se registran
en debug y no se propagan.

### 9.6 Captura y reset (POST)

`_handle_capture`:

```python
payload = _read_json_body()
label = payload.get("label", "")
camera_open = bool(camera.is_open)
frame = camera.get_frame() if camera_open else None
result = teach.capture(label, frame, camera_open=camera_open)
if result["ok"]:
    result["loaded"] = detector.reload_templates()
    200
else:
    422
```

`_handle_reset`: exige `label` string no vacío, llama a `teach.reset`, recarga y
responde 200/400.

`_handle_reload`: consume el body **solo si** hay `Content-Length` (valida rango),
recarga y responde `{ok, counts, samples}`. Acepta POST sin body.

`_handle_set_camera`: exige `{"url": "http://..."}` no vacía, valida esquema
`http`, `https` o `rtsp`, persiste con `set_camera_source()` y responde
`{ok, source}`. Una URL vacía o con otro esquema devuelve 400 y no cambia la
fuente; un fallo de escritura devuelve 500. Para volver a una webcam hay que
editar `CAMERA_INDEX`/borrar `data/camera_url.txt` y reiniciar el servicio.

### 9.7 Archivos estáticos y seguridad de rutas

`_serve_static(relative)`:

```python
relative = relative.split("?", 1)[0]
base = config.STATIC_DIR.resolve()
target = (base / relative).resolve()
if base != target and base not in target.parents:
    403
if not target.is_file():
    404
content_type = MIME_TYPES.get(suffix, "application/octet-stream")
```

Esto bloquea `..`, symlinks y rutas absolutas que escapen de `web/static/`.
`MIME_TYPES` cubre HTML, CSS, JS, JSON, imágenes, fuentes y texto.

### 9.8 Stream MJPEG

```
200
Content-Type: multipart/x-mixed-replace; boundary=frame
Cache-Control: no-store, no-cache, must-revalidate
Pragma: no-cache
Connection: close

bucle:
  frame = camera.get_frame() o placeholder
  data = encode_jpeg(frame, 85)
  b"---frame\r\nContent-Type: image/jpeg\r\nContent-Length: N\r\n\r\n" + data
  flush
  dormir hasta 1/STREAM_MAX_FPS
```

El placeholder se crea una vez por conexión con `make_status_frame()`. Al
desconectarse el cliente se captura `BrokenPipeError`/`ConnectionResetError`/
`OSError`, se marca `close_connection = True` y se registra en el log.

### 9.9 `main()`

1. Configura logging con `LOG_LEVEL` (formato con hora, nivel, hilo y logger).
2. Crea la app y el servidor. Si el puerto está ocupado, loguea la sugerencia
   `PORT=8010` y retorna 1.
3. `app.start()`; si falla, cierra el servidor y retorna 1.
4. Registra `SIGINT` y `SIGTERM` → `server.shutdown()` en un hilo.
5. `serve_forever(poll_interval=0.5)`; al salir, `server_close()` y `app.stop()`.
6. Retorna 0.

---

## 10. Frontend

### 10.1 Archivos

| Archivo | Rol |
|---|---|
| `web/index.html` | Estructura del detector: topbar, banners, video+canvas, panel, footer |
| `web/teach.html` | Estructura del modo enseñar: preview, 3 tarjetas de controles |
| `web/static/app.js` | Polling, dibujo de cajas/globos, panel, reconexión |
| `web/static/teach.js` | Captura, recarga, borrado, conteos |
| `web/static/style.css` | Tema oscuro sobrio con acentos dorados, sin CDNs ni fuentes externas |

### 10.2 `app.js`

Estado:

```js
const state = {
  boards: new Map(),      // id → ficha
  detections: [],         // última respuesta de /api/detections
  bubbles: [],            // rectángulos de globos para hit-testing
  selectedLabel: null,
  selectedMissingPolls: 0, // polls consecutivos sin el label seleccionado
  health: null,
  dpr: 1,
};
```

Constantes actuales de campo: `COLOR_BOX = "#c9a35a"` (dorado),
`COLOR_INK = "#191919"`, `COLOR_BUBBLE = "#f3efe6"`,
`COLOR_SCAN_QUAD = "#FFD400"` (marco del área de escaneo) y sombra
`rgba(201,163,90,.55)`; `POLL_DETECTIONS_MS = 250`, `POLL_HEALTH_MS = 2000`.
El suavizado usa `SMOOTH_WINDOW = 9`, `SMOOTH_ALPHA = 0.15`, `DEADBAND = 18`,
`FLIP_MARGIN = 20`, `GAP = 34` y una gracia de 1500 ms antes de ocultar el
globo. El auto-cierre del panel usa `MISSING_POLLS_TO_CLOSE = 8` (~2 s).
`drawScanQuad` dibuja el polígono amarillo fino (`lineWidth = 2`, sin relleno)
con los puntos de `health.scan_quad`, antes del early-return por “sin
detecciones”: el marco queda visible siempre que haya stream.

Funciones principales:

| Función | Descripción |
|---|---|
| `syncCanvas()` | Ajusta `canvas.width/height` al tamaño CSS × `min(devicePixelRatio, 2)` |
| `drawScanQuad(cw, ch)` | Polígono amarillo `#FFD400` del área de escaneo, sin relleno, con etiqueta “ÁREA DE ESCANEO” |
| `summaryLines(detection)` | `[short_name, "MCU: …", "RAM: …", "Reloj: …"]` (máx. 3 claves) |
| `drawBox(detection, cw, ch)` | Marco dorado con esquinas de visor (5 px si está seleccionado); devuelve el rect en px CSS |
| `drawBubble(detection, box, lines, cw, ch, occupied)` | Globo + colita; evita solapamientos |
| `draw()` | Limpia el canvas y dibuja todas las detecciones |
| `openPanel(label)` | Rellena y muestra el panel lateral |
| `closePanel()` | Oculta el panel y limpia la selección |
| `trackSelectedPresence()` | Cuenta polls sin el label abierto y cierra el panel a los 8 (~2 s) |
| `loadBoards()` | `GET /api/boards` → `Map` |
| `applyHealth(health)` | Banners, píldora, ms de inferencia y contadores |
| `pollDetections()` | `GET /api/detections` y `setTimeout` encadenado |
| `pollHealth()` | `GET /api/health` cada 2 s |

Detalles de dibujo:

- `canvas` se superpone al `<img>` con CSS; las cajas normalizadas se multiplican
  por `cw`/`ch` (tamaño CSS), no por el tamaño físico del canvas.
- `ctx.setTransform(dpr, 0, 0, dpr, 0, 0)` para dibujar en píxeles CSS.
- El globo se coloca arriba si hay lugar (`box.y - height - 14 >= 4`), si no
  abajo; si se sale por abajo, vuelve arriba. Se desplaza verticalmente para no
  chocar con globos ya dibujados (`GAP = 34`, `FLIP_MARGIN = 20`).
- La posición se estabiliza con mediana de 9 cuadros, EMA 0.15 y zona muerta de
  18 px; tras 1500 ms sin detección se oculta el globo.
- La colita es un triángulo con vértice en el borde de la caja, recortado a 4 px
  de los extremos.
- El hit-test recorre primero los globos (en orden inverso) y después las cajas.
- Auto-cierre del panel: tras cada poll exitoso, `trackSelectedPresence()`
  incrementa `selectedMissingPolls` si el `label` abierto no está en
  `state.detections`; a los 8 polls (~2 s) llama a `closePanel()`. El contador se
  reinicia en `openPanel()`, cuando el label reaparece y en `closePanel()`. Un
  error de red no incrementa el contador.

Reconexión del stream: en `error`, muestra “Reconectando stream…”, espera 1.5 s y
recarga `src` con `?t=Date.now()` para evitar caché.

### 10.3 `teach.js`

- `LABELS` mapea las 4 etiquetas a nombres legibles.
- `pollHealth()` cada 2.5 s actualiza conteos y píldora.
- `postJSON(url, body)` normaliza errores HTTP a `{ok:false, message}`.
- `captureBtn`: `POST /api/teach/capture` con `{label: select.value}`.
- `reloadBtn`: `POST /api/teach/reload` con `{}`.
- `resetBtn`: confirma y `POST /api/teach/reset` con la etiqueta actual.
- `resetAllBtn`: confirma y `POST /api/teach/reset` con `__all__`.
- `setBusy()` deshabilita los cuatro botones durante la operación.
- `pollHealthOnce()` refresca el estado 250 ms después de cada acción.

### 10.4 `style.css`

Variables principales:

```css
--bg:#17171b; --bg-soft:#202026; --panel:#f3efe6; --ink:#191919;
--green:#c9a35a; --green-dark:#a8853f; --accent:#c9a35a; --danger:#c0504d;
--border:1px solid rgba(201,163,90,.35); --radius:10px;
--mono-font:"JetBrains Mono","Fira Code","DejaVu Sans Mono",monospace;
```

Sin `@import`, sin `url()` remotos, sin Google Fonts. Todo se resuelve con
tipografías instaladas en el sistema.

---

## 11. Tests

### 11.1 Organización

| Archivo | Tests | Cobertura |
|---|---|---|
| `tests/test_specs.py` | 8 | Carga, validación, duplicados, formateadores, summary |
| `tests/test_detector.py` | 14 (9 funciones + 5 parametrizaciones) | Detección, 2 instancias, rechazo, recarga, IoU, label |
| `tests/test_api.py` | 19 | Endpoints, stream, teach, health, páginas, 404 |
| **Total** | **41** | — |

`conftest.py` inserta la raíz del proyecto en `sys.path` para que `import app`
funcione sin instalar el paquete.

### 11.2 Helpers (`tests/helpers.py`)

| Helper | Uso |
|---|---|
| `make_texture(seed, width, height, background)` | Imagen sintética con formas y texto (“UNO R4”, “RA4M1”, “ARDUINO”) |
| `write_template(root, label, image, name)` | Extrae ORB y guarda un `.npz` |
| `make_scene(width, height, background)` | Fondo oscuro uniforme |
| `place_texture(scene, texture, center, scale, angle, brightness)` | Rota/escala/pega la textura |
| `FakeCamera(frame, present, index)` | Cámara inyectable sin `/dev/video*` |

### 11.3 `ServerHarness`

Levanta la app con `FakeCamera`, `interval_ms=50` y puerto efímero (`port=0`),
en un hilo. Expone `base_url` y se usa como context manager. Cada test cierra
servidor y app.

### 11.4 Ejecución

```bash
source .venv/bin/activate

# Todo
pytest -q

# Solo detector
pytest -q tests/test_detector.py

# Un test
pytest -q tests/test_api.py::test_teach_reload_accepts_empty_body

# Con salida detallada
pytest -v
```

Resultado verificado: **41 passed** (5.32 s en notebook).

### 11.5 Mapa con los casos del plan

| Caso | Test |
|---|---|
| CT01 | `test_loads_four_boards_with_required_fields` |
| CT02 | `test_missing_field_raises_clear_error` |
| CT03 | `test_boards_endpoint` |
| CT04 | `test_detect_single_instance` |
| CT05 | `test_detect_two_instances` |
| CT06 | `test_blank_image_has_no_detections` |
| CT07 | `test_unrelated_texture_is_rejected` |
| CT08 | `test_health_endpoint_with_camera` |
| CT09 | `test_detections_endpoint_shape` |
| CT10 | `test_teach_capture_creates_sample` |
| CT11 | `test_teach_reset_removes_samples` |
| CT12 | `test_stream_mjpeg` |
| CT13 | `test_static_css` |
| CT14 | `test_health_without_camera` |
| CT15 | `test_health_without_templates` |
| CT16 | Cubierto por el frame de estado y el reintento (no simula desconexión física) |
| CT17 | Manual: `scripts/run_local.sh` |
| CT18 | Manual: cámara real / despliegue en UNO Q |

---

## 12. Cómo extender

### 12.1 Agregar una placa nueva

No hace falta tocar el detector: escanea cualquier carpeta dentro de
`data/templates/`. Los pasos son:

1. **Ficha técnica** en `data/boards.json` con `id` igual a la etiqueta
   (minúsculas, `[a-z0-9_]`, 2–40 caracteres).
2. **Etiqueta en los conteos**: agregar el ID a `config.DEFAULT_LABELS` para que
   `/api/health` la muestre en 0 antes de entrenar.
3. **Etiqueta en la UI de teach** (opcional pero recomendado):
   - `web/teach.html`: `<option value="<nueva_etiqueta>"><Nombre de la placa></option>`;
   - `web/static/teach.js`: entrada en `LABELS` con la misma etiqueta.
4. **Enseñar**: ir a `/teach`, elegir la etiqueta (o capturarla por API), tomar
   10–20 muestras.
5. **Verificar**: `/api/health` debe mostrar `loaded.<nueva_etiqueta> > 0` y el
   detector debe dibujar la placa.

> Los textos `<nueva_etiqueta>` y `<Nombre de la placa>` son marcadores: no son
> ids del catálogo vigente. Los 4 ids reales son `arduino_uno_r4_wifi`,
> `raspberry_pi_4`, `arduino_mkr_wifi_1010` y `esp32`.

> Si la etiqueta no está en `DEFAULT_LABELS`, igual funciona: el detector la
> carga desde el directorio y `templates.labels` la lista. Solo faltará en el
> listado de conteos en 0.

### 12.2 Cambiar el resumen del globo

El globo muestra `short_name` + las **3 primeras claves** de
`BoardSpecs.get_summary()`: `MCU`, `RAM`, `Reloj`. Para cambiar el contenido:

- editá `get_summary()` en `app/specs.py` (por ejemplo, agregar `"Flash"`), y
- tené en cuenta que `app.js` hace `Object.keys(summary).slice(0, 3)`: si querés
  más de tres líneas, ajustá también el frontend.

### 12.3 Cambiar el estilo visual

- Colores y tipografías: variables CSS en `web/static/style.css`.
- Recuadro y globo: `drawBox` y `drawBubble` en `web/static/app.js`.
- El canvas usa coordenadas CSS; si cambiás el layout, verificá `syncCanvas()`.

### 12.4 Agregar un endpoint

1. Agregá la rama en `do_GET` o `do_POST`.
2. Escribí un helper en `Handler` que use `_send_json`/`_send_error_json`.
3. Si necesita estado, exponelo en `DetectorTarjetasApp`.
4. Agregá un test en `tests/test_api.py` con `ServerHarness`.

### 12.5 Cambiar la fuente de video

`CameraCapture` está aislado: cualquier objeto con la interfaz de cámara
(`frame_queue`, `is_open`, `resolution`, `fps`, `get_frame()`,
`make_status_frame()`, `start()`, `stop()`) se puede inyectar en
`DetectorTarjetasApp(camera=...)`. Es el mismo mecanismo que usan los tests con
`FakeCamera`.

Para una cámara IP, habría que implementar un hilo que lea el stream (RTSP/HTTP)
y publique frames en la misma cola; el detector no cambia.

### 12.6 Agregar persistencia de detecciones

El detector no guarda historial: `_detections` es solo el último resultado. Si se
necesita, agregar una cola o un archivo en `BoardDetector.run()` después de
`detect_frame`, cuidando no bloquear el hilo.

---

## 13. Depuración

### 13.1 Logs

```bash
LOG_LEVEL=DEBUG python -m app.server
```

El formato incluye hora, nivel, nombre del hilo y logger:

```
2026-11-13 10:15:02,123 INFO [board-detector] app.detector: Plantillas recargadas: {'arduino_uno_r4_wifi': 8}
```

En la UNO Q:

```bash
sudo journalctl -u detector-tarjetas -f
sudo journalctl -u detector-tarjetas -n 100 --no-pager
```

### 13.2 Estado del sistema

```bash
curl -s http://localhost:8000/api/health | python3 -m json.tool
```

Revisar:

- `camera.present` y `camera.fps`;
- `templates.count`, `templates.labels`, `templates.by_label`, `templates.loaded`;
- `detector.last_inference_ms` e `inferences`.

### 13.3 Probar el detector con una imagen

```python
import queue
import cv2
from app.detector import BoardDetector

detector = BoardDetector(frame_queue=queue.Queue(maxsize=2))
frame = cv2.imread("placa.jpg")
for det in detector.detect_frame(frame):
    print(det.label, round(det.score, 3), det.inliers, det.box)
```

Si no detecta:

- verificar que `data/templates/` tenga archivos;
- imprimir `detector.template_counts`;
- probar con una imagen similar a la muestra;
- bajar temporalmente `SCORE_THRESHOLD` para ver candidatos (¡y restaurarlo!).

### 13.4 Inspeccionar un NPZ

```python
import numpy as np
path = "data/templates/arduino_uno_r4_wifi/<archivo>.npz"
with np.load(path, allow_pickle=False) as data:
    print(data.files)
    print(data["label"].item())
    print(data["descriptors"].shape, data["points"].shape)
```

### 13.5 Ver la cámara cruda

```bash
v4l2-ctl -d /dev/video0 --list-formats-ext
python3 - <<'PY'
import cv2
cap = cv2.VideoCapture(0)
print("abierta:", cap.isOpened())
print("resolución:", cap.get(cv2.CAP_PROP_FRAME_WIDTH), cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
ok, frame = cap.read()
print("frame:", ok, None if frame is None else frame.shape)
cap.release()
PY
```

### 13.6 Problemas típicos y causa

| Síntoma | Dónde mirar |
|---|---|
| No detecta nada | `templates.loaded`, cantidad/calidad de muestras, `score` |
| Detecta de más | `SCORE_THRESHOLD`, `MIN_INLIERS`, fondo de las muestras |
| Dos cajas sobre la misma placa | `NMS_IOU_THRESHOLD`, margen de consumo de keypoints |
| No separa dos copias | `MAX_INSTANCES_PER_TEMPLATE`, `INSTANCE_CENTER_DISTANCE_PX` |
| Latencia alta | `ORB_FEATURES`, `DETECTOR_INTERVAL_MS`, carga de la CPU |
| Stream cortado | red, `STREAM_MAX_FPS`, `JPEG_QUALITY`, logs de desconexión |
| Label con comillas | `normalize_label`, contenido del NPZ |
| `templates.by_label` no coincide con `loaded` | recarga pendiente o NPZ inválido |

### 13.7 Tests que fallan

```bash
python -m compileall app tests
pytest -q tests/test_detector.py -x -vv
```

Los tests de detección usan imágenes sintéticas con semilla fija. Si fallan
sistemáticamente, revisar cambios en `create_orb`, en los umbrales o en el orden
de `knnMatch`/`findHomography`.

---

## 14. Scripts de operación

### 14.1 `scripts/run_local.sh`

- Crea `.venv` si falta (`PYTHON_BIN`, `VENV_DIR` configurables).
- Instala `requirements-dev.txt` salvo `SKIP_INSTALL=1`.
- Exporta `PORT=8000`, `CAMERA_INDEX=0`, `HOST=0.0.0.0`.
- Ejecuta `python -m app.server` con `exec` (hereda señales).

### 14.2 `scripts/discover_unoq.sh`

- Detecta la interfaz global IPv4 (prioriza `192.168.`, luego `10.`).
- Permite `--subnet`, `--no-ping`, `--candidate`.
- Barre hosts con `ping` y prueba puertos TCP con `/dev/tcp` y `timeout`.
- Lee banner SSH y consulta `/api/health` en el puerto 8000.
- No modifica configuración de red.

### 14.3 `scripts/deploy.sh`

- Valida `SERVICE_NAME`, `SSH_USER`, `SSH_PORT`, `APP_PORT`, `SSH_HOST`,
  `ADB_SERIAL` y `REMOTE_DIR` (absoluto, sin `..`).
- Escapa con `quote_remote` (`printf %q`) todo lo que interpola en comandos
  remotos.
- `--try-defaults` prueba `arduino`, `unoq`, `root` con `sshpass`.
- Empaqueta con `tar` y excluye `.venv`, `venv`, `.git`, `__pycache__`,
  `.pytest_cache`, `PLAN.md`, `AUDIT_REPORT.md`, `data/templates/*`.
- Instala dependencias con `sudo -n`; si no hay sudo sin contraseña, sale con 3.
- Instala la unidad systemd renderizando los placeholders `@USER@`, `@GROUP@`,
  `@WORKDIR@` y `@PORT@`.
- Siembra `/etc/default/detector-tarjetas` **solo si no existe**; un redeploy
  nunca pisa `CAMERA_URL`/`CAMERA_INDEX` de la placa.
- Ejecuta `systemctl daemon-reload`, `enable` y `restart`; después verifica
  `is-enabled` = `enabled`, `is-active` = `active` y `/api/health`.
- `--via-adb --adb-serial <serial>` usa `adb push`/`adb shell` y sigue el mismo
  esquema (validando el serial con `[A-Za-z0-9_.:-]`).

Códigos de salida: `2` SSH, `3` sudo/servicio no habilitado, `4` adb ausente,
`5` health, `6` unidad existente con User/Group/Workdir distintos.

### 14.4 Unidad systemd

`systemd/detector-tarjetas.service` usa `/usr/bin/python3 -m app.server`,
`Restart=always`, `RestartSec=3`, `SupplementaryGroups=video`,
`NoNewPrivileges=true`, `PrivateTmp=true` y
`EnvironmentFile=-/etc/default/detector-tarjetas`. El despliegue reemplaza
usuario, directorio y puerto, y deja la unidad `enabled` para que arranque con
la placa. `systemd/detector-tarjetas.env.example` documenta las variables
`CAMERA_URL`, `CAMERA_INDEX`, `PORT` y `LOG_LEVEL`.

---

## 15. Decisiones de diseño y compromisos

| Tema | Decisión | Compromiso |
|---|---|---|
| Detección | ORB + homografía | Liviano y sin modelos; requiere enseñar y tolera menos escala extrema |
| Plantillas | Una por captura, sin promedio | Simple y robusto a variaciones; más archivos y más costo de matching |
| Matching | Ratio test 0.75 + RANSAC | Buen filtrado de falsos positivos; puede perder placas muy ocluidas |
| Instancias | `MAX_INSTANCES_PER_TEMPLATE = 1` | Una placa por etiqueta, sin cajas fantasma; no distingue dos copias iguales |
| Cajas | Normalizadas | Frontend independiente de la resolución; se pierde precisión absoluta |
| HTTP | `ThreadingHTTPServer` | Sin dependencias; menos features que un framework |
| Estado | En memoria, sin base de datos | Simple; se pierde historial al reiniciar |
| Teach | Recarga en caliente tras cada captura | Feedback inmediato; puede costar con muchas plantillas |
| Frontend | Canvas 2D | Control total del estilo visual; sin accesibilidad avanzada |
| Seguridad | LAN de confianza, sin auth | Cero fricción; no apto para internet |

### 15.1 Desviaciones respecto de `PLAN.md`

- Se agregaron `GET /api/teach/samples` y `POST /api/teach/reload` (extensiones
  razonables, documentadas y testeadas).
- No existen constantes `CAMERA_BACKEND` ni `MATCHER_NORM`: el backend se elige
  en runtime (`CAP_V4L2` → `CAP_ANY`) y el matcher se crea con `NORM_HAMMING`
  inline.
- CT17 y CT18 no están automatizados: requieren hardware real.

---

## 16. Checklist para contribuir

- [ ] `python -m compileall app tests` sin errores.
- [ ] `pytest -q` con 41 tests pasando (o más, si agregaste).
- [ ] Si tocaste `boards.json`, validá el JSON y los campos obligatorios.
- [ ] Si tocaste el detector, probá con una imagen real además de los tests.
- [ ] Si tocaste el frontend, verificá que no haya recursos externos (CDNs,
      fuentes, imágenes remotas).
- [ ] Si tocaste `deploy.sh`, mantené las validaciones y el escapado.
- [ ] Si agregaste una etiqueta, actualizá `DEFAULT_LABELS`, la UI de teach y la
      documentación.
- [ ] No commitees `data/templates/*.npz`, `.venv/`, `PLAN.md` ni
      `AUDIT_REPORT.md`.

---

**Fin de la documentación de desarrollador.** Para el punto de vista del usuario,
ver `docs/datasheet.md`; para el historial de decisiones, `docs/BITACORA.md`.
