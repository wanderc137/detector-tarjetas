# Bitácora de desarrollo · Detector de tarjetas

**Proyecto:** Detector de placas electrónicas por visión artificial para Arduino UNO Q
**Versión documentada:** 1.0.0
**Fecha de cierre de esta bitácora:** noviembre de 2026
**Estado:** código implementado, auditado y aprobado; despliegue físico en la UNO Q pendiente

---

## 1. Resumen del recorrido

| Etapa | Resultado |
|---|---|
| Análisis y plan | `PLAN.md` con arquitectura, endpoints, constantes y 18 casos de prueba |
| Implementación | 7 módulos Python, 2 páginas HTML, 3 archivos JS/CSS, 3 scripts, unidad systemd, 4 fichas JSON |
| Tests | 41 pruebas automatizadas con imágenes sintéticas y cámara falsa |
| Auditoría | Aprobada, 0 incidencias abiertas; 4 incidencias del ciclo 1 resueltas |
| Documentación | README, datasheet, developer y esta bitácora |
| Despliegue en UNO Q | **No completado**: la placa no fue accesible por red ni por ADB en el momento del desarrollo |

---

## 2. Problemas encontrados y cómo se resolvieron

### P-01 · La UNO Q no aparecía en la red

**Síntoma.** El barrido de la LAN mostraba un host vivo en `192.168.1.4`
(MAC `2a:71:1b:c4:cd:e7`) que respondía ping y ARP, pero **no tenía ningún puerto
TCP abierto**. No había SSH, ni HTTP, ni mDNS (`avahi` no estaba instalado).

**Diagnóstico.** El comportamiento es compatible con una UNO Q encendida pero con
SSH deshabilitado y sin servicio web. Las subredes típicas de Teltonika
(`192.168.2.1`, `192.168.0.1`, `10.0.0.1`, `192.168.88.1`) tampoco respondían.

**Solución.** Se documentaron tres caminos de despliegue:

1. habilitar SSH desde Arduino App Lab (`sudo systemctl enable --now ssh`);
2. usar el fallback `deploy.sh --via-adb` por USB-C;
3. copiar el proyecto manualmente desde App Lab.

**Estado.** El despliegue quedó pendiente por falta de acceso físico a la placa;
el código y los scripts están listos.

**Lección.** Verificar el acceso al hardware **antes** de planificar el despliegue.
Un host que responde ping no garantiza que se pueda administrar.

---

### P-02 · El label de las plantillas se leía con comillas (I-01)

**Síntoma.** Al guardar una plantilla, `np.savez` almacena el label como un array
0-d de Unicode. Al cargarlo con `str(data["label"])`, el resultado era
`"'arduino_uno_r3'"` (con comillas simples). Eso rompía la coincidencia con
`boards.json`: la ficha no se resolvía, el globo mostraba el label crudo y
`templates.loaded` tenía claves distintas a las de `templates.by_label`.

**Solución.** Se agregó `normalize_label(raw, fallback)` en `app/detector.py`:

- usa `.item()` para extraer el escalar del array;
- decodifica bytes UTF-8;
- elimina comillas simples o dobles residuales de archivos viejos;
- devuelve un fallback si queda vacío.

Se usa al guardar y al cargar. Tests: `test_normalize_label` (5 casos) y
`test_template_label_roundtrip_is_clean`.

**Lección.** Nunca asumir que `str(np.array("x"))` devuelve `"x"`. Serializar
siempre con un tipo explícito y probar el *roundtrip* guardar → cargar.

---

### P-03 · El modo enseñar aceptaba el frame sintético (I-02)

**Síntoma.** Cuando no había cámara, `CameraCapture` publicaba un frame gris con
el texto `CAMARA NO DISPONIBLE`. `TeachMode.capture()` solo rechazaba
`frame is None`, así que era posible guardar ese frame de error como muestra y
contaminar las plantillas.

**Solución.** Se agregó el parámetro `camera_open` a `TeachMode.capture()` y el
servidor ahora verifica `camera.is_open` antes de obtener el frame:

```python
camera_open = bool(getattr(self.app.camera, "is_open", False))
frame = self.app.camera.get_frame() if camera_open else None
result = self.app.teach.capture(label, frame, camera_open=camera_open)
```

Sin cámara se devuelve **422** y no se escribe ningún archivo. Tests:
`test_teach_capture_without_camera` y
`test_teach_capture_rejects_synthetic_status_frame`.

**Lección.** Un frame “válido” no siempre es un frame “útil”. Los estados de error
deben modelarse explícitamente, no inferirse del contenido.

---

### P-04 · `/api/teach/reload` exigía body (I-03)

**Síntoma.** El endpoint se documentó como un POST simple, pero llamaba a
`_read_json_body()`, que rechaza peticiones sin `Content-Length` o con cuerpo
vacío. `curl -X POST .../api/teach/reload` devolvía 400, aunque el frontend
funcionaba porque enviaba `{}`.

**Solución.** El body pasó a ser **opcional**: si hay `Content-Length` se valida
el rango y se consume; si no, se recarga igual. Respuesta:
`{"ok": true, "counts": {...}, "samples": {...}}`. Test:
`test_teach_reload_accepts_empty_body`.

**Lección.** La API documentada y la implementada deben coincidir; los tests
deben incluir el caso “sin body”.

---

### P-05 · Inyección de comandos en `deploy.sh` (I-04)

**Síntoma.** Variables como `REMOTE_DIR`, `SSH_USER` o `SERVICE_NAME` se
interpolaban dentro de comandos `ssh`, `scp` y `adb shell`. Un valor con una
comilla simple podía ejecutar comandos arbitrarios en la placa.

**Solución.** Se agregaron *allowlists* estrictas:

- identificadores: `[A-Za-z0-9_-]+`;
- puertos: entero 1–65535;
- host: `[A-Za-z0-9_.:-]+`;
- ruta remota: absoluta, `[A-Za-z0-9_./-]`, sin `..`.

Además, todo valor interpolado se escapa con `printf '%q'` (`quote_remote`) como
defensa en profundidad.

**Lección.** Las comillas simples no son una sanitización. Validar primero y
escapar después.

---

### P-06 · El hilo de cámara podía quedar bloqueado en `read()`

**Síntoma.** Al detener la aplicación, `VideoCapture.read()` puede bloquearse
esperando un frame. Un `join` corto dejaba el hilo vivo y el proceso colgado.

**Solución.** `CameraCapture.stop()` hace un cierre en dos etapas:

1. señaliza `_stop_event` y espera 3 s;
2. si sigue vivo, libera el `VideoCapture` desde afuera y espera 2 s más.

Se evita liberar la captura en paralelo con una lectura normal porque el backend
V4L2 de OpenCV puede abortar.

**Lección.** Los hilos que leen de hardware necesitan un plan de desbloqueo
explícito; `join` solo no alcanza.

---

### P-07 · Homografías degeneradas generaban cajas enormes

**Síntoma.** En pruebas con poco texto visible, RANSAC podía devolver una
homografía que proyectaba las esquinas muy lejos, produciendo cajas gigantes o
fuera del frame.

**Solución.** `_build_detection()` valida:

- proyección finita;
- `boundingRect` con ancho y alto > 1;
- área del polígono ≥ 20 % del área de su bounding box
  (`MIN_POLYGON_FILL_RATIO`);
- recorte al frame;
- caja mínima 8×8 px;
- área entre 2 % y 90 % del frame.

**Lección.** Toda geometría estimada necesita validaciones de cordura antes de
llegar a la UI.

---

### P-08 · No se detectaban dos copias de la misma placa

**Síntoma.** Con dos UNO R3 iguales en el cuadro, el ratio test descartaba los
matches de la segunda porque los descriptores de la primera seguían presentes.

**Solución.** `_match_template()` repite el proceso hasta 4 veces por plantilla.
Después de aceptar una instancia, **elimina del frame todos los keypoints dentro
de su caja** (con 8 px de margen), no solo los inliers. Así el ratio test vuelve a
ser discriminante. El clustering final agrupa centros a menos de 40 px.

**Lección.** Para objetos repetidos, el matching uno-a-uno no alcanza: hay que
“consumir” la evidencia de la instancia ya encontrada.

---

### P-09 · Los acentos no se ven en el frame de estado

**Síntoma.** `cv2.putText` con fuentes Hershey no representa acentos ni “Ñ”.

**Solución.** Los mensajes del frame sintético se escriben sin acentos:
`CAMARA NO DISPONIBLE`, `CAMARA DESCONECTADA`, `Revisa el cable USB o el indice
CAMERA_INDEX`. La interfaz HTML sí usa acentos porque el navegador renderiza
UTF-8 correctamente.

**Lección.** Separar el texto de la UI (HTML, UTF-8) del texto dibujado con
OpenCV (ASCII).

---

### P-10 · Los tests de API necesitaban una cámara

**Síntoma.** Probar el servidor completo requería `/dev/video*` y una placa real.

**Solución.** Se creó `FakeCamera` en `tests/helpers.py`, que expone la misma
interfaz que `CameraCapture` y devuelve un frame fijo. `DetectorTarjetasApp`
acepta `camera=` por inyección. `ServerHarness` levanta el servidor en un puerto
efímero (`port=0`) y lo cierra como context manager.

**Lección.** Inyectar hardware desde el principio hace que el 90 % del sistema sea
testeable sin dispositivos.

---

### P-11 · Las imágenes sintéticas no siempre producían keypoints

**Síntoma.** Al principio, texturas planas o con pocos bordes generaban menos de
50 keypoints y los tests fallaban de forma intermitente.

**Solución.** `make_texture()` dibuja 90 formas (círculos, rectángulos, líneas)
con colores aleatorios y agrega texto grande (“UNO R3”, “ATmega328P”, “ARDUINO”).
Con semilla fija, la textura es reproducible y rica en esquinas. `write_template`
falla explícitamente si no hay keypoints, para no ocultar el problema.

**Lección.** Los datos de test también se diseñan: deben tener la textura
suficiente para ejercitar el algoritmo.

---

## 3. Enfoques descartados y por qué

| Enfoque | Por qué se descartó |
|---|---|
| **Red neuronal (YOLO, MobileNet, etc.)** | Requiere descargar pesos, GPU o mucha CPU; rompe la premisa de operación sin internet y sin modelos externos. Además, para 4 placas conocidas, ORB es suficiente. |
| **SIFT / SURF** | Más robustos a escala, pero más lentos y con consideraciones de patentes/licencias históricas. ORB es más liviano para ARM64. |
| **AKAZE** | Buena alternativa, pero más costoso que ORB y sin ventaja clara para placas con textura impresa. |
| **Template matching por correlación (`matchTemplate`)** | Muy sensible a rotación, escala e iluminación; no sirve para placas inclinadas. |
| **Contornos + color** | Las placas comparten formas y colores (verde, negro, azul); no discriminan entre modelos. |
| **OCR del silkscreen** | Requiere Tesseract u otro motor, más dependencias y resolución; además el texto suele estar en ángulos difíciles. |
| **Base de datos (SQLite) para plantillas** | Los `.npz` en carpetas son simples, respaldables y recargables sin migraciones. |
| **Promediar descriptores de varias muestras** | Perdería información espacial y la homografía necesaria para proyectar la caja. |
| **Un solo NPZ gigante por etiqueta** | Dificulta el borrado selectivo, la recarga y el diagnóstico; un archivo corrupto afectaría toda la etiqueta. |
| **WebSocket para las detecciones** | El polling cada 250 ms es más simple y suficiente; el stream ya usa MJPEG. |
| **Framework web (Flask/FastAPI)** | Agrega dependencias y complejidad; `http.server` cubre las necesidades y corre en la UNO Q sin pip. |
| **`rsync` para el despliegue** | Puede no estar instalado en la placa; `tar` sobre SSH es universal. |
| **Exponer la app a internet con autenticación básica** | Fuera de alcance; se documentó como LAN de confianza y se recomienda VPN/proxy. |
| **Bind a `127.0.0.1` por defecto** | El caso de uso principal es abrirla desde el notebook; se usa `0.0.0.0` y se advierte del riesgo. |
| **Detección de texto para validar la placa** | Costo extra y falsos negativos; la homografía + inliers ya validan. |

---

## 4. Decisiones técnicas y su justificación

### D-01 · ORB en lugar de aprendizaje profundo

**Contexto:** UNO Q ARM64, sin GPU, sin internet, 4 placas conocidas.
**Decisión:** ORB + BFMatcher Hamming + RANSAC.
**Justificación:** OpenCV ya está instalado; el costo de inferencia entra en
200–300 ms; no hay pesos que descargar; el usuario puede enseñar en el lugar.
**Consecuencia:** hay que entrenar y la robustez depende de las muestras.

### D-02 · Plantillas por muestra, no un modelo por placa

**Decisión:** cada captura genera un `.npz` independiente.
**Justificación:** permite borrar muestras individuales, recargar en caliente,
diagnosticar archivos corruptos y respaldar de forma granular.
**Consecuencia:** más archivos y más costo de matching; se mitiga con 4–8 muestras
por placa.

### D-03 · Cajas normalizadas

**Decisión:** `box = {x, y, w, h}` en `[0,1]`.
**Justificación:** el frontend no depende de 640×480; el canvas escala con CSS y
`devicePixelRatio`.
**Consecuencia:** se pierde precisión absoluta; para dibujar alcanza.

### D-04 · Dos consumidores de frames

**Decisión:** `last_frame` para el stream y `frame_queue` (tamaño 2) para el
detector.
**Justificación:** varios clientes de video no compiten con el detector; el
detector siempre procesa lo más reciente.
**Consecuencia:** si el detector es lento, se saltea frames (deseable).

### D-05 · `ThreadingHTTPServer` y no un framework

**Decisión:** biblioteca estándar.
**Justificación:** cero dependencias en la placa; el stream y la API se atienden
en hilos separados; el código es legible y auditable.
**Consecuencia:** rutas y manejo de errores manuales; no hay middlewares.

### D-06 · Modo enseñar con recarga inmediata

**Decisión:** tras cada captura, `detector.reload_templates()`.
**Justificación:** feedback inmediato: el usuario ve la placa detectada sin
reiniciar.
**Consecuencia:** con muchas plantillas, cada captura reescanea el directorio;
aceptable para decenas de archivos.

### D-07 · Fichas en JSON con validación estricta

**Decisión:** `data/boards.json` como fuente de verdad, validado al cargar.
**Justificación:** editable sin tocar código; los errores se detectan temprano con
mensajes claros (campo, línea, columna).
**Consecuencia:** un JSON roto hace que `/api/boards` devuelva 500; se documenta
cómo corregirlo.

### D-08 · Seguridad proporcional al contexto

**Decisión:** sin autenticación, CORS `*`, bind `0.0.0.0`.
**Justificación:** LAN de confianza, demo educativa, acceso desde cualquier
dispositivo sin fricción.
**Consecuencia:** no apto para internet; se advierte explícitamente y se
recomienda VPN/proxy.

### D-09 · Despliegue por `tar` sobre SSH y fallback ADB

**Decisión:** `tar` en lugar de `rsync`; `adb` como plan B.
**Justificación:** `tar` y `ssh` están en cualquier Debian; `adb` cubre el caso de
SSH cerrado y USB-C disponible.
**Consecuencia:** el script es más largo, pero cubre los escenarios reales.

### D-10 · Preservar plantillas en cada despliegue

**Decisión:** excluir `data/templates/*` del `tar` y solo crear el directorio.
**Justificación:** las plantillas son el “modelo” y viven en la placa; un
despliegue de código no debe borrarlas.
**Consecuencia:** si se quiere replicar el entrenamiento, hay que copiarlas
explícitamente.

---

## 5. Lecciones aprendidas

1. **El hardware primero.** Antes de escribir el despliegue, confirmar que la
   placa es accesible y que SSH está habilitado. Un host que responde ping puede
   no tener ningún puerto abierto.
2. **Serializar con tipos explícitos.** `np.savez` + `str()` produjo un bug sutil
   que afectaba labels, fichas y conteos. Probar siempre el *roundtrip*.
3. **Modelar los estados de error.** “Sin cámara” no es lo mismo que “frame
   negro”: el frame sintético debe rechazarse explícitamente en teach.
4. **Inyectar hardware.** `FakeCamera` permitió 41 tests sin cámara ni placa; el
   diseño con interfaces mínimas paga desde el primer día.
5. **Validar y escapar.** Las allowlists + `printf %q` son la diferencia entre un
   script de despliegue y una puerta trasera.
6. **La geometría necesita cordura.** Área, fill ratio, tamaño mínimo y clamping
   evitan que una homografía mala arruine la UI.
7. **Consumir evidencia para múltiples instancias.** Para objetos repetidos, hay
   que quitar del frame lo ya explicado por la primera detección.
8. **Separar UI de dibujo técnico.** HTML soporta UTF-8; `cv2.putText` no. Los
   mensajes del frame de estado van sin acentos.
9. **Documentar la API real, no la ideal.** `/api/teach/reload` y
   `/api/teach/samples` se agregaron durante la implementación y se documentaron
   como extensiones.
10. **El despliegue también se prueba.** `--via-adb`, los códigos de salida y los
    mensajes de sudo son parte del producto, no un detalle.
11. **La calidad de las muestras manda.** Más muestras no siempre es mejor:
    variar ángulo, distancia y luz vale más que 30 fotos idénticas.
12. **Los tests sintéticos son útiles si tienen textura.** Una imagen plana no
    ejercita ORB; una con formas y texto sí.

---

## 6. Verificación y evidencia

| Evidencia | Resultado |
|---|---|
| `python -m compileall app tests` | Sin errores |
| `pytest -q` | **41 passed** en 5.32 s (notebook) |
| Auditoría estática | Aprobada, 0 incidencias abiertas |
| Incidencias del ciclo 1 | I-01, I-02, I-03, I-04 resueltas y reauditadas |
| Prueba manual local | `scripts/run_local.sh` + navegador + `/api/health` |
| Despliegue UNO Q | Pendiente (sin acceso de red ni ADB) |

### Cobertura por archivo de test

| Archivo | Tests |
|---|---|
| `tests/test_specs.py` | 8 |
| `tests/test_detector.py` | 14 (incluye 5 parametrizaciones) |
| `tests/test_api.py` | 19 |

---

## 7. Pendientes y próximos pasos

| Prioridad | Tarea |
|---|---|
| Alta | Conectar la UNO Q por USB-C y ejecutar `deploy.sh --via-adb`, o habilitar SSH desde App Lab |
| Alta | Verificar en la placa `python3 -c "import cv2"`, `/api/health` y el stream real |
| Alta | Entrenar las 4 placas en el lugar definitivo (10–20 muestras cada una) |
| Media | Medir la inferencia real en ARM64 y ajustar `ORB_FEATURES`/`DETECTOR_INTERVAL_MS` |
| Media | Agregar un test de integración con video grabado (no hardware) |
| Media | Evaluar un modo “solo detección” sin stream para ahorrar ancho de banda |
| Baja | Agregar autenticación opcional y TLS para escenarios fuera de la LAN |
| Baja | Exportar métricas (detecciones/min, latencia) a un archivo o endpoint |
| Baja | Soporte para más placas (ESP32, STM32, etc.) con el mismo flujo de teach |

---

## 8. Registro de cambios relevantes

| Cambio | Motivo |
|---|---|
| `normalize_label()` | Corregir labels con comillas (I-01) |
| `camera_open` en `TeachMode.capture()` | Evitar muestras del frame sintético (I-02) |
| Body opcional en `/api/teach/reload` | Cumplir la API documentada (I-03) |
| Allowlists + `quote_remote` en `deploy.sh` | Evitar inyección de comandos (I-04) |
| `MIN_POLYGON_FILL_RATIO` | Descartar homografías degeneradas |
| Consumo de keypoints por caja | Detectar múltiples instancias iguales |
| `FakeCamera` + `ServerHarness` | Tests de API sin hardware |
| `/api/teach/samples` | Consultar conteos sin recargar |
| Documentación `docs/` | Cierre del pipeline |

---

**Fin de la bitácora.** Las decisiones aquí registradas buscan que un futuro
proyecto similar no repita los mismos errores: verificar hardware temprano,
probar serialización, modelar errores, inyectar dependencias y validar todo lo
que se interpola en comandos.
