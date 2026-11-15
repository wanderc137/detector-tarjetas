# 🔍 Detector de tarjetas · Arduino UNO Q

**Visión artificial en tiempo real, 100 % local, sin modelos externos.**

Aplicación web auto-contenida que **reconoce placas electrónicas** (Arduino UNO R3,
Arduino Nano, Raspberry Pi 4 y Raspberry Pi 5) en el video de una webcam USB y
dibuja **recuadros verdes con globos cómic** que muestran la información técnica
de cada placa sobre la imagen en vivo.

Corre en el **lado Linux de una Arduino UNO Q** (Debian ARM64) y también en un
**notebook x86_64** para desarrollo. El navegador solo abre la página: toda la
captura, la inferencia y el servidor viven en la placa.

> **Versión:** 1.0.0 · **Estado:** código auditado y aprobado · **Tests:** 41/41
> pasando en el notebook. **Despliegue en UNO Q:** realizado (systemd + cámara IP,
> ver [Despliegue en la UNO Q](DESPLIEGUE_UNOQ.md) y [Estado actual](docs/ESTADO_ACTUAL.md)).

---

## Tabla de contenidos

1. [Qué es](#qué-es)
2. [Características](#características)
3. [Principio de funcionamiento](#principio-de-funcionamiento)
4. [Hardware y conexión](#hardware-y-conexión)
5. [Requisitos](#requisitos)
6. [Puesta en marcha local (notebook)](#puesta-en-marcha-local-notebook)
7. [Despliegue en la Arduino UNO Q](#despliegue-en-la-arduino-uno-q)
8. [Modo enseñar paso a paso](#modo-enseñar-paso-a-paso)
9. [Uso diario](#uso-diario)
10. [Fichas técnicas incluidas](#fichas-técnicas-incluidas)
11. [API HTTP](#api-http)
12. [Rendimiento y ajustes](#rendimiento-y-ajustes)
13. [Resolución de problemas](#resolución-de-problemas)
14. [Seguridad y advertencias](#seguridad-y-advertencias)
15. [Estado actual verificado](#estado-actual-verificado)
16. [Estructura del repositorio](#estructura-del-repositorio)
17. [Licencia y uso](#licencia-y-uso)

---

## Qué es

`detector-tarjetas` es una aplicación de **detección de objetos por plantillas
visuales**. No usa redes neuronales, no descarga pesos, no consulta servicios en
la nube y no necesita GPU. La detección se resuelve con **OpenCV ORB +
homografía RANSAC** y un conjunto de plantillas que el propio usuario enseña
desde el navegador apuntando la cámara a cada placa.

El resultado se presenta como una **escena de cómic**: cada placa detectada
recibe un recuadro verde con esquinas estilo visor y un globo de diálogo blanco
con borde negro que resume tres datos (MCU, RAM y reloj). Al hacer clic sobre la
placa o el globo se abre un panel lateral con la ficha técnica completa.

| Aspecto | Detalle |
|---|---|
| Tipo | Aplicación web de visión en tiempo real |
| Detección | ORB + BFMatcher (Hamming) + ratio test + homografía RANSAC |
| Placas de fábrica | Arduino UNO R3, Arduino Nano (R3), Raspberry Pi 4, Raspberry Pi 5 |
| Entrada | Webcam USB UVC (`/dev/video0`, 640×480, MJPG) |
| Salida | Stream MJPEG + API JSON + canvas con recuadros y globos |
| Hardware destino | Arduino UNO Q, lado Linux Debian ARM64 |
| Alternativa | Notebook Linux x86_64 con webcam |
| Dependencias externas | Ninguna en runtime (solo OpenCV del sistema) |
| Autenticación | No (pensada para LAN de confianza) |

### Lo que **no** es

- No es un clasificador genérico de componentes electrónicos: reconoce **solo las
  etiquetas que fueron enseñadas**.
- No identifica marcas, números de serie ni texto (no hace OCR).
- No es un servicio de internet: no hay TLS, usuarios ni cuentas.
- No funciona "de fábrica" sin entrenar: las fichas técnicas vienen cargadas,
  pero **las plantillas ORB se generan al enseñar cada placa**.

---

## Características

- 🎥 **Stream MJPEG** en vivo a 640×480, calidad JPEG 85, hasta 15 fps.
- 🧠 **Detección multinstancia**: varias placas distintas y varias copias del
  mismo modelo en el mismo cuadro, separadas por clustering espacial.
- 💬 **Globos cómic** dibujados en `<canvas>`: nombre corto + MCU + RAM + reloj,
  con colita que apunta a la placa y evitación de solapamientos.
- 🖱️ **Ficha completa al clic**: panel lateral con MCU/SoC, reloj, RAM,
  almacenamiento, GPIO, voltaje, dimensiones, conectividad, año, descripción y
  enlace a la documentación oficial.
- 🎓 **Modo enseñar** (`/teach`): captura de muestras por etiqueta, recarga en
  caliente, borrado por etiqueta o total, todo desde el navegador.
- 🛟 **Tolerancia a fallos**: sin cámara se muestra un frame de estado y la API
  reporta `camera.present=false`; reconexión automática cada 2 s; sin plantillas
  la UI invita a entrenar; cámara ausente, plantillas corruptas y cuerpos JSON
  inválidos se manejan sin tumbar el servidor.
- 🧩 **Sin dependencias de frontend**: HTML, CSS y JavaScript puros, sin CDNs,
  sin frameworks, sin fuentes externas. Tipografías del sistema.
- 🔁 **Mismo código x86_64 / ARM64**: `opencv-python-headless` en desarrollo y
  `python3-opencv` del sistema en la UNO Q.
- 🧪 **41 tests automatizados** con imágenes sintéticas (sin necesidad de cámara
  ni hardware).

---

## Principio de funcionamiento

### Pipeline completo

```
                        /dev/video0  (webcam USB UVC)
                              │
                              │  V4L2 · MJPG · 640×480 · 15 fps
                              ▼
                 ┌─────────────────────────┐
                 │  CameraCapture (hilo)   │
                 │  · abre V4L2 y reintenta│
                 │  · publica last_frame   │
                 │  · frame_queue(maxsize=2)│
                 └───────┬─────────┬───────┘
                         │         │
        last_frame ──────┘         └──────► frame_queue
             │                                    │
             ▼                                    ▼
     /stream.mjpg                     ┌──────────────────────────────┐
     (MJPEG a 15 fps)                 │  BoardDetector (hilo)        │
                                      │  1. escala de grises         │
                                      │  2. ORB: 500 keypoints       │
                                      │  3. knnMatch Hamming k=2     │
                                      │  4. ratio test de Lowe 0.75  │
                                      │  5. homografía RANSAC (5 px) │
                                      │  6. caja + score + NMS       │
                                      └──────────────┬───────────────┘
                                                     │ list[Detection]
                                                     ▼
                                      GET /api/detections  ◄── polling 250 ms
                                                     │
                                                     ▼
                                      <canvas>: recuadro verde + globo cómic
```

### 1. Captura

El hilo `CameraCapture` abre la cámara con el backend **V4L2** (y cae a
`CAP_ANY` si el driver no lo acepta), pide formato **MJPG**, 640×480 y 15 fps,
y publica cada frame en dos lugares distintos:

- `last_frame`: lo consume el stream MJPEG (siempre el frame más reciente, sin
  bloquear).
- `frame_queue`: cola acotada a **2** frames para el detector. Si se llena, se
  descarta el frame viejo: al detector le interesa el presente, no acumular
  atraso.

Si la cámara no está disponible, publica un **frame sintético de estado**
(`CAMARA NO DISPONIBLE`) y reintenta abrirla cada 2 segundos. El texto se dibuja
sin acentos porque las fuentes Hershey de OpenCV no los representan.

### 2. Extracción de características: ORB

Por cada frame, el detector convierte a grises y calcula **ORB** (*Oriented FAST
and Rotated BRIEF*):

- **FAST** detecta esquinas de forma muy rápida.
- **BRIEF** describe cada esquina con un vector binario de 256 bits (32 bytes),
  invariante a rotación.
- Se piden como máximo **500 keypoints** (`ORB_FEATURES`), con umbral de borde 31
  y parche 31, puntuación Harris.

La ventaja para este proyecto: ORB ya viene dentro de OpenCV, es liviano para
ARM64 sin GPU y funciona bien con la **textura impresa de las placas**
(silkscreen, serigrafía, conectores, pines, componentes).

### 3. Matching: BFMatcher + ratio test de Lowe

Cada plantilla guardada se compara contra los descriptores del frame con
`BFMatcher(NORM_HAMMING)` y `knnMatch(k=2)`. Para cada descriptor de la plantilla
se obtienen sus dos mejores candidatos en el frame; el match se acepta solo si:

```
distancia(mejor) < 0.75 × distancia(segundo mejor)
```

Es el **ratio test de Lowe**: si el segundo candidato está casi tan cerca como el
primero, el match es ambiguo y se descarta. Esto elimina la mayoría de los falsos
positivos antes de la geometría.

### 4. Homografía RANSAC

Con los matches aceptados se estima una **homografía** de 3×3 con
`cv2.findHomography(..., cv2.RANSAC, 5.0)`. La homografía describe cómo se
transforma el plano de la imagen enseñada al plano del frame actual (perspectiva,
rotación, escala). RANSAC descarta los matches que no son geométricamente
consistentes; solo sobreviven los **inliers**.

Se exige un mínimo de **10 matches** y **15 inliers**. Luego se proyectan las
cuatro esquinas del área útil de la plantilla al frame, se obtiene su *bounding
box* y se valida:

- polígono finito y no degenerado (el área del polígono debe ser al menos el 20 %
  del área de su bounding box);
- caja de al menos 8×8 px;
- área entre **2 % y 90 %** del frame.

### 5. Score y falsos positivos

El score combina tres señales:

```
score = 0.6 × (inliers / matches)        proporción de matches consistentes
      + 0.3 × (1 − distancia_media/90)   calidad del apareamiento
      + 0.1 × min(1, área/2% × 0.5)      tamaño relativo de la detección
```

Una detección se publica solo si `score ≥ 0.35`. Después se aplica **NMS**
(supresión de no máximos, IoU > 0.30) y se **agrupan** detecciones de la misma
etiqueta cuyos centros estén a menos de **40 px**, conservando la de mayor score.

### 6. Varias instancias del mismo modelo

Para admitir dos UNO R3 iguales en el mismo cuadro, el detector repite el
matching hasta **4 veces por plantilla**. Después de encontrar una instancia,
**elimina de la búsqueda todos los keypoints del frame que caen dentro de su
caja** (con 8 px de margen), no solo los inliers. Así el ratio test vuelve a ser
discriminante para la segunda placa idéntica. Cada instancia recibe un
`instance_id` (0, 1, 2…) ordenado por posición horizontal.

### 7. Del JSON al canvas

El servidor publica las detecciones con **cajas normalizadas** `[0, 1]` respecto
del tamaño del frame. El navegador hace polling cada **250 ms**, escala las cajas
al tamaño real del `<img>` (incluyendo `devicePixelRatio` y `ResizeObserver`) y
dibuja:

- recuadro verde `#00ff3c` con glow y esquinas estilo visor;
- globo blanco con borde negro de 3 px, texto en tipografía cómic del sistema;
- colita triangular hacia la placa;
- reubicación automática si el globo se saldría de pantalla o pisaría a otro.

### ¿Por qué no usa modelos externos?

| Motivo | Consecuencia |
|---|---|
| **Sin internet en el dispositivo** | Nada que descargar, nada que actualizar, nada que falle sin red. |
| **OpenCV ya está instalado** | Cero dependencias nuevas en la UNO Q (`python3-opencv`). |
| **Enseñanza en el lugar** | Las plantillas se capturan con la cámara y la luz reales del lugar de uso; no hay *domain gap* entre dataset y realidad. |
| **Privacidad** | Ni una imagen sale de la LAN; no hay telemetría. |
| **Costo/espacio** | Sin pesos de modelos, sin GPU, sin RAM extra. |

La contrapartida es que **hay que enseñar cada placa** y que la robustez depende
de la calidad de las muestras (ver [Modo enseñar](#modo-enseñar-paso-a-paso)).
Para cuatro placas conocidas y una LAN de confianza, el intercambio es muy
favorable.

---

## Hardware y conexión

### Modo producción: todo corre en la UNO Q

```
                    ┌───────────────────────────────────────────────┐
                    │        Notebook / PC / teléfono               │
                    │   Navegador:  http://<ip-unoq>:8000/          │
                    │   (solo HTML/CSS/JS: no procesa video)        │
                    └──────────────────────┬────────────────────────┘
                                           │  Wi-Fi · LAN de confianza
                                           ▼
                    ┌───────────────────────────────────────────────┐
                    │        Router Teltonika (LAN + DHCP)          │
                    │        p. ej. 192.168.1.0/24                  │
                    └──────────────────────┬────────────────────────┘
                                           │  Ethernet o Wi-Fi
                                           ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  Arduino UNO Q                                                           │
│                                                                          │
│  ┌────────────────────────────────────┐  ┌────────────────────────────┐  │
│  │ Lado Linux · Debian ARM64          │  │ Lado MCU · STM32U585       │  │
│  │                                    │  │ (Zephyr; no se usa en      │  │
│  │  app/server.py    :8000            │  │  esta versión)             │  │
│  │  app/camera.py    V4L2             │  └────────────────────────────┘  │
│  │  app/detector.py  ORB/RANSAC       │                                  │
│  │  data/templates/  plantillas .npz  │                                  │
│  └───────────────┬────────────────────┘                                  │
│                  │  USB (hub USB-C si hace falta)                        │
└──────────────────┼───────────────────────────────────────────────────────┘
                   ▼
          ┌──────────────────┐
          │  Webcam USB UVC  │   640×480 · MJPG · 15 fps
          │  (ej. Logitech)  │
          └──────────────────┘
```

Puntos clave:

- **La aplicación corre en la UNO Q**, no en el notebook. El notebook solo abre
  el navegador y consume el stream MJPEG y el JSON.
- La webcam va conectada a la **UNO Q** (directamente o mediante un hub USB-C si
  no queda un puerto libre). No va a la notebook.
- El router Teltonika solo transporta tráfico IP; **no se modifica su
  configuración**.
- La placa necesita una dirección IP alcanzable desde el notebook. El modo
  habitual es DHCP en la misma LAN.

### Modo desarrollo: todo corre en el notebook

```
┌──────────────┐   USB   ┌─────────────────────────────────────┐
│ Webcam USB   │────────►│ Notebook Linux x86_64               │
└──────────────┘         │  .venv + opencv-python-headless     │
                         │  python -m app.server (:8000)       │
                         │  Navegador: http://localhost:8000/  │
                         └─────────────────────────────────────┘
```

Es el modo recomendado para probar cambios de código, porque compilar y
reiniciar es inmediato y no depende del estado de la placa.

### Alimentación y puertos

| Elemento | Conexión | Nota |
|---|---|---|
| Arduino UNO Q | Fuente USB-C con suficiente corriente | Alimenta también el hub y la webcam |
| Webcam USB | Puerto USB de la UNO Q o hub USB-C | UVC estándar; MJPG recomendado |
| Red | Ethernet o Wi-Fi de la UNO Q | La IP debe ser fija o reservada en DHCP |
| Notebook | Wi-Fi/LAN al mismo router | Solo navegador |

> Si la webcam consume más corriente de la que entrega el puerto, usá un **hub
> USB-C con alimentación**. Los síntomas típicos son desconexiones intermitentes
> y `camera.present=false` en `/api/health`.

---

## Requisitos

### Hardware

- Arduino UNO Q (lado Linux Debian ARM64) **o** notebook Linux x86_64.
- Webcam USB **UVC** (clase de video estándar, sin drivers propietarios).
- Hub USB-C si hace falta conectar la webcam y alimentar la placa.
- Router con DHCP y LAN accesible desde el navegador.

### Software

| Componente | Notebook (desarrollo) | UNO Q (producción) |
|---|---|---|
| Python | 3.10+ (probado con 3.12) | `python3` del sistema |
| OpenCV | `opencv-python-headless>=4.8,<6` (venv) | `python3-opencv` (apt) |
| Utilidades de cámara | `v4l-utils` (opcional) | `v4l-utils` (recomendado) |
| Tests | `pytest>=7.4`, `requests>=2.31` | no se instalan |
| Servidor | biblioteca estándar `http.server` | biblioteca estándar |
| Navegador | Chrome/Firefox/Edge actualizado | cualquiera en la LAN |

> **No hay `requirements.txt` de runtime.** En la UNO Q se usa el paquete del
> sistema `python3-opencv` (sin `pip`, sin venv). Las dependencias del notebook
> están en **`requirements-dev.txt`**.

### Verificar la cámara

```bash
ls -l /dev/video*
# crw-rw----+ 1 root video 81, 0 ... /dev/video0

v4l2-ctl --list-devices          # requiere v4l-utils
v4l2-ctl -d /dev/video0 --all | head -40
```

Si el usuario no pertenece al grupo `video`:

```bash
sudo usermod -aG video "$USER"
# cerrar sesión y volver a entrar (o reiniciar el servicio)
```

---

## Puesta en marcha local (notebook)

### Opción A — script todo-en-uno (recomendado)

```bash
cd detector-tarjetas
./scripts/run_local.sh
```

El script:

1. crea `.venv` si no existe (`python3 -m venv .venv`);
2. activa el entorno e instala `requirements-dev.txt`;
3. exporta `PORT=8000`, `CAMERA_INDEX=0`, `HOST=0.0.0.0`;
4. ejecuta `python -m app.server`.

Luego abrí:

```
http://localhost:8000/
```

Variables útiles del script:

```bash
PORT=8010 ./scripts/run_local.sh          # otro puerto
CAMERA_INDEX=2 ./scripts/run_local.sh     # /dev/video2
HOST=127.0.0.1 ./scripts/run_local.sh     # solo localhost
SKIP_INSTALL=1 ./scripts/run_local.sh     # no reinstalar dependencias
PYTHON_BIN=python3.12 ./scripts/run_local.sh
VENV_DIR=/tmp/venv-detector ./scripts/run_local.sh
```

### Opción B — manual

```bash
python3 -m venv venv
source venv/bin/activate          # Linux / macOS
# venv\Scripts\activate           # Windows

pip install -r requirements-dev.txt
python -m app.server
```

### Verificar que está vivo

```bash
curl -s http://localhost:8000/api/health | python3 -m json.tool
```

Respuesta esperada (abreviada):

```json
{
  "status": "ok",
  "camera": {"present": true, "resolution": [640, 480], "fps": 14.8, "index": 0},
  "templates": {
    "count": 0,
    "labels": [],
    "by_label": {
      "arduino_nano": 0,
      "arduino_uno_r3": 0,
      "raspberry_pi_4": 0,
      "raspberry_pi_5": 0
    },
    "loaded": {}
  },
  "detector": {"last_inference_ms": 0.0, "inferences": 0},
  "uptime_s": 3.1,
  "version": "1.0.0"
}
```

- `camera.present=true` → la webcam entrega frames.
- `camera.present=false` → la app funciona igual, pero muestra el frame de estado.
- `templates.count=0` → todavía no hay plantillas: hay que entrenar.

### Tests

```bash
source venv/bin/activate
python -m compileall app tests
pytest -q
```

Resultado verificado en este equipo: **41 passed**.

---

## Despliegue en la Arduino UNO Q

> **Prerrequisito:** la UNO Q debe ser alcanzable por red y tener SSH habilitado.
> Si no lo está, saltá a [Fallback USB-C / ADB / App Lab](#fallback-usb-c--adb--app-lab).

### Paso 1 — Descubrir la placa en la LAN

```bash
./scripts/discover_unoq.sh
# o acotando el rango:
./scripts/discover_unoq.sh --subnet 192.168.1.0/24
# solo tabla ARP, sin barrido:
./scripts/discover_unoq.sh --no-ping
```

El script es **no invasivo**: no toca routers ni configuración de red. Muestra
interfaces e IPs del notebook, vecinos ARP, hace ping a cada host y prueba los
puertos **22, 80, 443, 8000 y 8080** con timeout corto. Clasifica:

| Hallazgo | Interpretación | Acción |
|---|---|---|
| Puerto 8000 responde `/api/health` | La app **ya está desplegada** | Abrir `http://<ip>:8000/` |
| Puerto 22 abierto (con banner SSH) | SSH disponible | `./scripts/deploy.sh --host <ip> --user arduino` |
| Host vivo y **cero puertos TCP** | Probable UNO Q con SSH deshabilitado | Fallback USB-C / ADB / App Lab |
| Nada responde | Otra subred o Wi-Fi distinta | Repetir con `--subnet` correcta |

Variables: `PING_TIMEOUT=1`, `PORT_TIMEOUT=1`, `MAX_PARALLEL=32`,
`PORTS="22 80 443 8000 8080"`; opción `--candidate <ip>` para priorizar un
candidato conocido en el informe.

### Paso 2 — Desplegar por SSH

```bash
# con clave SSH ya configurada
./scripts/deploy.sh --host 192.168.1.4 --user arduino

# probando contraseñas por defecto (requiere sshpass)
./scripts/deploy.sh --host 192.168.1.4 --user arduino --try-defaults

# con contraseña explícita (requiere sshpass; no se guarda en el repo)
UNOQ_PASSWORD='...' ./scripts/deploy.sh --host 192.168.1.4 --user arduino

# puerto o directorio alternativos
./scripts/deploy.sh --host 192.168.1.4 --app-port 8080 \
                    --remote-dir /home/arduino/detector-tarjetas
```

Qué hace `deploy.sh`, en orden:

1. Valida todos los parámetros con *allowlists* (`[A-Za-z0-9_-]`, puertos
   1–65535, ruta remota absoluta sin `..`) y los escapa con `printf %q` antes de
   interpolarlos en comandos remotos.
2. Prueba SSH (clave; opcionalmente `sshpass` con `--try-defaults`, que intenta
   `arduino`, `unoq` y `root`).
3. Copia el proyecto con **`tar` sobre SSH** (sin `rsync`), excluyendo `.venv`,
   `venv`, `.git`, `__pycache__`, `.pytest_cache`, `PLAN.md`, `AUDIT_REPORT.md` y
   las muestras `data/templates/*`. **Las muestras existentes en la placa se
   preservan** (solo se asegura que el directorio exista).
4. Verifica `import cv2`; si falta, instala `python3-opencv` y `v4l-utils` con
   `apt` usando `sudo -n`. Si `sudo` pide contraseña, imprime los comandos
   exactos y termina con código 3.
5. Genera la unidad systemd con el usuario, directorio y `PORT` elegidos, la
   instala en `/etc/systemd/system/`, agrega el usuario al grupo `video` si
   existe, hace `daemon-reload`, `enable` y `restart`.
6. Espera 3 s y verifica `http://127.0.0.1:8000/api/health` desde la propia
   placa (`curl` o `urllib`). Si no responde, muestra los últimos 25 renglones de
   `journalctl`.

Al final imprime:

```
Web local en la UNO Q : http://127.0.0.1:8000/
Web desde la notebook : http://<ip>:8000/
Stream MJPEG          : http://<ip>:8000/stream.mjpg
```

Códigos de salida: `2` SSH inaccesible, `3` hace falta sudo manual, `4` falta
`adb`, `5` el servicio no respondió.

### Paso 3 — Servicio systemd (manual, si hace falta)

La unidad versionada es `systemd/detector-tarjetas.service`:

```ini
[Unit]
Description=Detector de tarjetas electronicas (ORB) - Arduino UNO Q
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=arduino
WorkingDirectory=/home/arduino/detector-tarjetas
Environment=PYTHONUNBUFFERED=1
Environment=PORT=8000
Environment=CAMERA_INDEX=0
Environment=LOG_LEVEL=INFO
ExecStart=/usr/bin/python3 -m app.server
Restart=always
RestartSec=3
SupplementaryGroups=video
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

Instalación manual:

```bash
sudo install -m 644 systemd/detector-tarjetas.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now detector-tarjetas
sudo systemctl status detector-tarjetas
sudo journalctl -u detector-tarjetas -f
```

> `deploy.sh` ajusta `User=`, `WorkingDirectory=` y `Environment=PORT=` con `sed`
> antes de instalar la unidad. Si el grupo `video` no existe en la placa, elimina
> `SupplementaryGroups=video` para que el servicio no falle.

### Fallback USB-C / ADB / App Lab

Si la UNO Q **responde ping pero no tiene puertos TCP abiertos** (SSH
deshabilitado), no hace falta tocar la red. Conectala por **USB-C** a la notebook.

#### Opción A — ADB

```bash
adb devices
./scripts/deploy.sh --via-adb
# con varios dispositivos:
./scripts/deploy.sh --via-adb --adb-serial <serial>
```

El modo `--via-adb`:

1. empaqueta el proyecto en un `.tar.gz` temporal (mismos excludes);
2. `adb push` a `/tmp/detector-tarjetas.tar.gz` y extrae en
   `/home/arduino/detector-tarjetas`;
3. verifica `import cv2`; si falta, intenta
   `sudo apt-get update && sudo apt-get install -y python3-opencv v4l-utils`;
4. copia e instala la unidad systemd y hace `enable --now`;
5. imprime el comando de verificación con `urllib`.

Si `adb` no está instalado, el script termina con código 4 y sugiere las
alternativas (instalarlo con autorización del usuario, usar App Lab, o habilitar
SSH).

#### Opción B — Arduino App Lab

1. Abrí **Arduino App Lab** y conectá la UNO Q por USB-C.
2. Entrá a la **terminal del lado Linux**.
3. Habilitá SSH y volvé al despliegue por red:

   ```bash
   sudo systemctl enable --now ssh
   ```

   o copiá la carpeta del proyecto al directorio de la app.
4. Instalá dependencias:

   ```bash
   sudo apt-get update && sudo apt-get install -y python3-opencv v4l-utils
   ```

5. Probá a mano:

   ```bash
   cd detector-tarjetas
   python3 -m app.server
   ```

#### Opción C — habilitar SSH desde la placa

Si tenés teclado/monitor o acceso a la terminal de App Lab:

```bash
sudo systemctl enable --now ssh
ip -4 addr show          # anotar la IP
```

Y desde la notebook:

```bash
./scripts/discover_unoq.sh
./scripts/deploy.sh --host <ip> --user arduino
```

---

## Modo enseñar paso a paso

La detección **no funciona sin plantillas**. Enseñar una placa genera uno o más
archivos `.npz` en `data/templates/<etiqueta>/`; el detector los carga en
caliente y empieza a reconocerla.

### Procedimiento

1. **Abrí `/teach`** desde la barra superior del detector (“🎓 Modo enseñar”).
2. **Elegí la etiqueta** en el desplegable:
   `arduino_uno_r3`, `arduino_nano`, `raspberry_pi_4`, `raspberry_pi_5`.
3. **Encuadrá la placa**:
   - buena luz difusa, sin sombras duras;
   - fondo **contrastado y liso** (una hoja oscura o una mesa mate funciona bien);
   - la placa debe ocupar entre el **30 % y el 70 %** del cuadro;
   - incluí el **silkscreen** (texto impreso) y los conectores: son la mejor
     textura para ORB.
4. **Capturá 10–20 muestras** variando:
   - ángulo de inclinación (±30° en horizontal y vertical);
   - distancia (20–60 cm);
   - posición dentro del cuadro (centro y bordes);
   - iluminación (luz cálida/fría, sombra suave).
   La UI sugiere “3 y 5 muestras” para una prueba rápida; para uso real conviene
   **10–20** por placa.
5. **Recargá**: cada captura ya recarga el detector automáticamente. El botón
   “🔄 Entrenar / recargar” fuerza una recarga completa desde disco.
6. **Verificá** en `/api/health` que `templates.count` subió y que
   `templates.loaded` muestra la etiqueta.
7. **Volvé al detector** (`/`) y apuntá la cámara: la placa debe aparecer con
   recuadro y globo.

### Qué guarda cada muestra

Cada captura guarda **un frame completo procesado con ORB**, no un recorte:

| Campo `NPZ` | Tipo | Contenido |
|---|---|---|
| `label` | Unicode 0-d | Etiqueta limpia (`arduino_uno_r3`) |
| `descriptors` | `uint8 (N, 32)` | Descriptores ORB |
| `points` | `float32 (N, 2)` | Coordenadas `(x, y)` de cada keypoint |
| `sizes` | `float32 (N,)` | Tamaño del parche |
| `angles` | `float32 (N,)` | Orientación |
| `responses` | `float32 (N,)` | Respuesta del detector |
| `octaves` | `int32 (N,)` | Octava de la pirámide |
| `class_ids` | `int32 (N,)` | `-1` si no aplica |
| `image_size` | `int32 (2,)` | `(ancho, alto)` de la imagen origen |

Se requiere un mínimo de **50 keypoints** por muestra. Si ORB encuentra menos,
el servidor responde **422** con el mensaje *“Pocos puntos característicos
(N/50). Acercá la placa, mejorá la luz o evitá fondos lisos.”* y **no** escribe
el archivo.

### Consejos de calibración

| ✅ Hacer | ❌ Evitar |
|---|---|
| Fondo mate y liso, de color opuesto a la placa | Fondo de madera veteada o cables enredados |
| Luz difusa (nublado, lámpara rebotada) | Reflejos directos sobre el PCB |
| Incluir el texto impreso y los conectores | Tapar el microcontrolador con el dedo |
| Variar ángulo, distancia y posición | 10 fotos desde el mismo lugar |
| Encuadrar la placa completa con margen | Recortar los bordes de la placa |
| Enseñar también la cara trasera si se va a mostrar | Enseñar solo una cara y girarla |
| Capturar con la cámara y la luz definitivas | Entrenar con otra cámara/otra habitación |

### Borrar muestras

En `/teach`, sección “3. Muestras guardadas”:

- **🗑️ Borrar muestras de esta etiqueta** → elimina solo `data/templates/<label>/*.npz`.
- **Borrar todas** → envía `{"label":"__all__"}` y borra todas las muestras de
  todas las etiquetas.

Ambas acciones piden confirmación y recargan el detector al terminar.

---

## Uso diario

1. **Abrí** `http://<ip-unoq>:8000/` (o `http://localhost:8000/` en el notebook).
2. **Esperá el primer frame**. El placeholder “Esperando video…” desaparece
   cuando llega el stream.
3. **Apuntá la cámara** a una o más placas.
4. **Interpretá la escena**:
   - recuadro verde con esquinas → placa detectada;
   - globo blanco → nombre corto + `MCU`, `RAM`, `Reloj`;
   - píldora de estado → `en vivo · N fps`, `sin cámara` o `sin conexión`.
5. **Hacé clic** sobre la placa o su globo para abrir la ficha completa en el
   panel lateral. Se cierra con `×` o con la tecla `Esc`.
6. **Varias placas a la vez**: cada etiqueta distinta se dibuja por separado; dos
   copias del mismo modelo reciben `instance_id` 0 y 1 y se dibujan como dos
   recuadros independientes.

### Señales de la interfaz

| Elemento | Significado |
|---|---|
| Banner amarillo “No se detecta la cámara” | `camera.present=false`; se muestra un frame de estado |
| Banner azul “Todavía no hay plantillas” | `templates.count=0`; hay que ir a `/teach` |
| Píldora `en vivo · N fps` | Cámara entregando frames; N es la media móvil |
| Píldora `sin cámara` | Cámara ausente o ocupada |
| Píldora `sin conexión` | El polling a `/api/detections` o `/api/health` falló |
| `inferencia N ms` bajo el video | Duración de la última inferencia |
| Pie de página | `N detección(es) · M muestra(s) · K etiqueta(s) entrenada(s)` |

### Atajos

| Acción | Cómo |
|---|---|
| Abrir ficha | Clic en la placa o en el globo |
| Cerrar ficha | Botón `×` o tecla `Esc` |
| Ir a enseñar | Botón “🎓 Modo enseñar” |
| Volver al detector | “← Volver al detector” en `/teach` |

---

## Fichas técnicas incluidas

`data/boards.json` trae cuatro fichas completas. Estos son los datos que se
muestran en el panel lateral y los que resumen los globos:

| Etiqueta | Placa | MCU / SoC | Reloj | RAM | Almacenamiento | GPIO | Voltaje | Dimensiones | Año |
|---|---|---|---|---|---|---|---|---|---|
| `arduino_uno_r3` | Arduino UNO R3 | ATmega328P (AVR 8 bits) | 16 MHz | 2 KB | 32 KB | 20 | 5 V | 68.6 × 53.4 mm | 2012 |
| `arduino_nano` | Arduino Nano (R3) | ATmega328P (AVR 8 bits) | 16 MHz | 2 KB | 32 KB | 22 | 5 V | 45 × 18 mm | 2008 |
| `raspberry_pi_4` | Raspberry Pi 4 Model B | Broadcom BCM2711 (Cortex-A72, 4 núcleos) | 1.5 GHz | 2 GB | microSD | 40 | 5 V | 85 × 56 mm | 2019 |
| `raspberry_pi_5` | Raspberry Pi 5 | Broadcom BCM2712 (Cortex-A76, 4 núcleos) | 2.4 GHz | 4 GB | microSD | 40 | 5 V | 85 × 56 mm | 2023 |

Conectividad:

| Placa | Conectividad |
|---|---|
| UNO R3 | USB-B, UART, I2C, SPI, ICSP |
| Nano | Mini-USB, UART, I2C, SPI, ICSP |
| Pi 4 | Wi-Fi 5, Bluetooth 5.0, Gigabit Ethernet, USB 3.0, micro-HDMI, GPIO 40 pines |
| Pi 5 | Wi-Fi 5, Bluetooth 5.0, Gigabit Ethernet, USB 3.0, micro-HDMI, PCIe 2.0 x1, GPIO 40 pines |

> **Los datos son editables.** `data/boards.json` es la fuente de verdad de la
> interfaz. Si agregás una placa, agregá también su etiqueta y enseñala desde
> `/teach`. El archivo se valida al cargar y los cambios se detectan por
> `mtime`/tamaño sin reiniciar el servidor.

---

## API HTTP

Todo se sirve desde el mismo puerto (8000 por defecto), sin autenticación y con
CORS `*`. Las respuestas JSON usan `Cache-Control: no-store`; los estáticos se
cachean 1 hora.

| Método | Ruta | Descripción | Códigos |
|---|---|---|---|
| GET | `/` · `/index.html` | Página principal (detector) | 200 |
| GET | `/teach` · `/teach/` · `/teach.html` | Página del modo enseñar | 200 |
| GET | `/static/*` | CSS/JS/imágenes (con protección de *path traversal*) | 200, 403, 404 |
| GET | `/stream.mjpg` | Stream MJPEG `multipart/x-mixed-replace` | 200 |
| GET | `/api/detections` | Detecciones actuales + datos de ficha | 200 |
| GET | `/api/boards` | Fichas técnicas completas + formateadas + resumen | 200, 500 |
| GET | `/api/health` | Estado de cámara, plantillas y detector | 200 |
| GET | `/api/teach/samples` | Conteo de muestras por etiqueta | 200 |
| GET | `/favicon.ico` | Sin contenido | 204 |
| POST | `/api/teach/capture` | Captura una muestra del frame actual | 200, 400, 422, 500 |
| POST | `/api/teach/reset` | Borra muestras de una etiqueta o todas | 200, 400 |
| POST | `/api/teach/reload` | Recarga plantillas en caliente (body opcional) | 200, 400 |
| OPTIONS | cualquiera | Preflight CORS | 204 |
| GET | ruta desconocida | Error JSON | 404 |

### Ejemplos con `curl`

```bash
BASE=http://localhost:8000

# Salud del sistema
curl -s "$BASE/api/health" | python3 -m json.tool

# Detecciones actuales
curl -s "$BASE/api/detections" | python3 -m json.tool

# Fichas técnicas (4 placas, con formatted y summary)
curl -s "$BASE/api/boards" | python3 -m json.tool

# Conteo de muestras por etiqueta
curl -s "$BASE/api/teach/samples" | python3 -m json.tool

# Capturar una muestra para Arduino UNO R3
curl -s -X POST "$BASE/api/teach/capture" \
     -H 'Content-Type: application/json' \
     -d '{"label":"arduino_uno_r3"}' | python3 -m json.tool

# Recargar plantillas (no requiere body)
curl -s -X POST "$BASE/api/teach/reload" | python3 -m json.tool

# Borrar las muestras de una etiqueta
curl -s -X POST "$BASE/api/teach/reset" \
     -H 'Content-Type: application/json' \
     -d '{"label":"arduino_uno_r3"}' | python3 -m json.tool

# Borrar TODAS las muestras
curl -s -X POST "$BASE/api/teach/reset" \
     -H 'Content-Type: application/json' \
     -d '{"label":"__all__"}' | python3 -m json.tool

# Guardar 3 segundos de stream MJPEG (multipart concatenado, no un JPEG único)
curl -s --max-time 3 "$BASE/stream.mjpg" -o /tmp/stream.mjpg
```

### `GET /api/detections`

```json
{
  "timestamp": 1763068800.123,
  "detections": [
    {
      "label": "arduino_uno_r3",
      "score": 0.912,
      "box": {"x": 0.2141, "y": 0.3056, "w": 0.4125, "h": 0.3187},
      "instance_id": 0,
      "inliers": 87,
      "name": "Arduino UNO R3",
      "short_name": "UNO R3",
      "summary": {"MCU": "ATmega328P", "RAM": "2 KB", "Reloj": "16 MHz"}
    }
  ]
}
```

- `box` está normalizado a `[0,1]` respecto del frame (640×480).
- `score` está redondeado a 3 decimales; `box` a 4.
- `name`, `short_name` y `summary` se resuelven desde `boards.json`; si la
  etiqueta no tiene ficha, se repite el `label` y `summary` queda vacío.

### `GET /api/health`

```json
{
  "status": "ok",
  "camera": {"present": true, "resolution": [640, 480], "fps": 14.7, "index": 0},
  "templates": {
    "count": 12,
    "labels": ["arduino_nano", "arduino_uno_r3"],
    "by_label": {"arduino_nano": 4, "arduino_uno_r3": 8, "raspberry_pi_4": 0, "raspberry_pi_5": 0},
    "loaded": {"arduino_nano": 4, "arduino_uno_r3": 8}
  },
  "detector": {"last_inference_ms": 248.3, "inferences": 1532},
  "uptime_s": 421.7,
  "version": "1.0.0"
}
```

- `templates.by_label` incluye las cuatro etiquetas conocidas aunque estén en 0.
- `templates.labels` solo lista las que tienen muestras.
- `templates.loaded` refleja lo que el detector tiene realmente en memoria.
- `camera.index` es el índice configurado (`CAMERA_INDEX`).

### `POST /api/teach/capture`

```json
// request
{"label": "arduino_uno_r3"}

// 200 OK
{
  "ok": true,
  "message": "Muestra guardada (312 keypoints). Total: 8.",
  "keypoints": 312,
  "samples": 8,
  "file": "arduino_uno_r3/20261113-101502-123-a1b2c3.npz",
  "loaded": {"arduino_uno_r3": 8}
}
```

Errores típicos:

| Situación | Código | `message` |
|---|---|---|
| Etiqueta inválida | 422 | `Etiqueta inválida: usá entre 2 y 40 caracteres [a-z0-9_] ...` |
| Cámara no entrega imágenes | 422 | `La cámara no está entregando imágenes reales...` |
| Menos de 50 keypoints | 422 | `Pocos puntos característicos (N/50)...` |
| Body ausente/inválido/grande | 400 | `Falta el header Content-Length` / `JSON inválido` / ... |

### `POST /api/teach/reset`

```json
// request
{"label": "arduino_uno_r3"}     // o {"label": "__all__"}

// 200 OK
{"ok": true, "message": "Muestras borradas de 'arduino_uno_r3': 8.", "removed": 8,
 "loaded": {}}
```

### `POST /api/teach/reload`

```json
{"ok": true, "counts": {"arduino_uno_r3": 8}, "samples": {"arduino_uno_r3": 8, ...}}
```

Acepta POST **sin body** y también con `{}`. Es la extensión del código respecto
del plan original (junto con `/api/teach/samples`).

---

## Rendimiento y ajustes

### Valores esperados

| Métrica | Valor |
|---|---|
| Resolución de captura | 640 × 480 |
| Formato | MJPG (V4L2) |
| FPS de cámara objetivo | 15 |
| FPS de stream MJPEG | hasta 15 (limitado por `STREAM_MAX_FPS`) |
| Calidad JPEG | 85 |
| Intervalo mínimo entre inferencias | 250 ms (≈4 Hz) |
| Inferencia objetivo en ARM64 | 200–300 ms |
| Ancho de banda aproximado | 1–3 Mbps a 640×480 y ~12 fps |

La inferencia y el stream son independientes: aunque el detector tarde 300 ms,
el video sigue a 15 fps porque el stream lee `last_frame` sin esperar al
detector.

### Variables de entorno

| Variable | Default | Uso |
|---|---|---|
| `PORT` | `8000` | Puerto HTTP |
| `HOST` | `0.0.0.0` | Interfaz de escucha |
| `CAMERA_INDEX` | `0` | Índice de `/dev/videoN` |
| `ORB_FEATURES` | `500` | Máximo de keypoints ORB |
| `BOARDS_JSON` | `data/boards.json` | Ruta alternativa de fichas |
| `TEMPLATE_DIR` | `data/templates` | Ruta alternativa de plantillas |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

### Constantes de ajuste (`app/config.py`)

| Constante | Default | Efecto |
|---|---|---|
| `DETECTOR_INTERVAL_MS` | `250` | Menos → más reactivo y más CPU |
| `ORB_FEATURES` | `500` | Menos → más rápido, menos robusto |
| `LOWE_RATIO` | `0.75` | Más bajo → matches más estrictos |
| `MIN_MATCH_COUNT` | `10` | Mínimo de matches para intentar homografía |
| `MIN_INLIERS` | `15` | Mínimo de inliers RANSAC |
| `RANSAC_REPROJ_THRESHOLD` | `5.0` px | Tolerancia de reproyección |
| `SCORE_THRESHOLD` | `0.35` | Umbral de publicación |
| `MIN_BOX_AREA_RATIO` | `0.02` | Caja mínima (2 % del frame) |
| `MAX_BOX_AREA_RATIO` | `0.90` | Caja máxima (90 % del frame) |
| `NMS_IOU_THRESHOLD` | `0.30` | Solapamiento para suprimir |
| `INSTANCE_CENTER_DISTANCE_PX` | `40` | Agrupación de instancias |
| `MAX_INSTANCES_PER_TEMPLATE` | `4` | Tope de copias por plantilla |
| `MIN_POLYGON_FILL_RATIO` | `0.20` | Descarte de homografías degeneradas |
| `TEMPLATE_MIN_KEYPOINTS` | `50` | Mínimo para aceptar una muestra |
| `JPEG_QUALITY` | `85` | Calidad del stream |
| `STREAM_MAX_FPS` | `15` | Techo del stream |
| `MAX_JSON_BODY_BYTES` | `65536` | Tamaño máximo de body JSON |

### Si la UNO Q va justa

1. Bajá `ORB_FEATURES=350`.
2. Subí `DETECTOR_INTERVAL_MS=350` o `400`.
3. Reducí `STREAM_MAX_FPS` o `JPEG_QUALITY` si el Wi-Fi es el cuello de botella.
4. Enseñá menos muestras por etiqueta (calidad > cantidad) si el arranque tarda
   en recargar plantillas.
5. Evitá más de 4–6 plantillas simultáneas por etiqueta.

---

## Resolución de problemas

| Síntoma | Causa probable | Solución |
|---|---|---|
| **Cámara negra o ausente** (banner “No se detecta la cámara”) | `/dev/video0` no existe, está ocupado o faltan permisos | `ls -l /dev/video*`; desconectar otros programas; `sudo usermod -aG video $USER`; revisar `CAMERA_INDEX` |
| Frame de estado `CAMARA NO DISPONIBLE` | La app arrancó sin cámara | Conectar la webcam; esperar 2 s de reintento; revisar `journalctl` |
| Frame de estado `CAMARA DESCONECTADA` | La cámara dejó de entregar frames | Revisar cable/hub alimentado; el hilo reintenta cada 2 s |
| **“Pocos puntos característicos (N/50)”** | Placa pequeña, desenfocada, sin luz o fondo liso | Acercar, enfocar, iluminar de costado, usar fondo contrastado |
| **Sin plantillas** (banner azul, no detecta nada) | `data/templates/` vacío | Entrenar desde `/teach`; verificar `templates.count` en `/api/health` |
| La placa está entrenada pero **no se detecta** | Ángulo/luz muy distintos; score < 0.35; caja < 2 % del frame | Capturar más variaciones; acercar; revisar `score` e `inliers` en `/api/detections` |
| Detecta **otra placa** como la entrenada | Plantillas poco discriminativas | Enseñar el silkscreen y los conectores; evitar fondos repetidos; subir `SCORE_THRESHOLD` |
| Se detecta y **desaparece** al mover | ORB no es invariante a cambios extremos de escala | Mantener distancia moderada (20–60 cm); agregar muestras a otras distancias |
| **Globos desalineados** | El canvas todavía no sincronizó con el `<img>` | Esperar el primer frame; redimensionar la ventana fuerza el `ResizeObserver` |
| **Puerto 8000 ocupado** | Otro servicio en la placa o notebook | `PORT=8010 ./scripts/run_local.sh`; en systemd, cambiar `Environment=PORT=` y reiniciar |
| **SSH no disponible** (host vivo, cero puertos) | UNO Q con SSH deshabilitado | `./scripts/deploy.sh --via-adb` o habilitar SSH desde App Lab (`sudo systemctl enable --now ssh`) |
| `deploy.sh` termina con código 3 | `sudo` pide contraseña | Ejecutar los comandos impresos a mano y repetir con `--skip-apt` |
| `deploy.sh` termina con código 5 | El servicio no respondió | `systemctl status detector-tarjetas`; `journalctl -u detector-tarjetas -n 50` |
| **Servicio systemd** reinicia en bucle | Falta `python3-opencv`, ruta incorrecta o cámara sin permisos | `journalctl`; `python3 -c "import cv2"`; revisar `User=`, `WorkingDirectory=` y `SupplementaryGroups=video` |
| `templates.loaded` no coincide con `by_label` | Plantillas corruptas o recarga pendiente | `POST /api/teach/reload`; revisar warnings del log (`Plantilla inválida ...`) |
| El stream se corta al cambiar de red | El navegador perdió la conexión TCP | El cliente reconecta solo a los 1.5 s con *cache-buster* |
| `/api/boards` devuelve 500 | `boards.json` inválido o campo faltante | Leer el mensaje de error (incluye archivo, línea y campo); corregir el JSON |
| `POST /api/teach/capture` devuelve 400 | Falta `Content-Length` o el JSON está malformado | Enviar `Content-Type: application/json` y un objeto válido |

### Diagnóstico rápido

```bash
# ¿Está el proceso?
ps aux | grep "app.server"

# ¿Qué dice el servicio?
sudo systemctl status detector-tarjetas
sudo journalctl -u detector-tarjetas -n 100 --no-pager

# ¿La cámara existe y quién la usa?
ls -l /dev/video*
sudo fuser -v /dev/video0

# ¿Qué ve OpenCV?
python3 -c "import cv2; c=cv2.VideoCapture(0); print(c.isOpened()); print(c.read()[0]); c.release()"

# ¿La API responde?
curl -s http://127.0.0.1:8000/api/health | python3 -m json.tool
```

---

## Seguridad y advertencias

> ⚠️ **Esta aplicación está pensada para una LAN de confianza.** No tiene
> autenticación, no usa TLS, no distingue usuarios y permite que cualquiera que
> llegue al puerto 8000 lea el video, consulte las fichas, capture muestras o
> **borre las plantillas**.

- **No la expongas a internet.** Si necesitás acceso remoto, usá una VPN o un
  proxy con autenticación y TLS por delante.
- **CORS `*`** está habilitado para facilitar pruebas; no lo cambies a un origen
  público sin analizar el impacto.
- **El stream muestra la cámara en vivo**: apuntala solo a lo que querés mostrar.
- **Permisos de cámara**: el usuario del servicio debe pertenecer al grupo
  `video` (`SupplementaryGroups=video` en systemd). No ejecutes el servidor como
  `root` para “resolver” permisos.
- **No se descargan modelos ni datasets**: cualquier propuesta de “mejorar” la
  detección bajando pesos externos rompe la premisa del proyecto y la operación
  sin internet.
- **Respaldá `data/templates/` y `data/boards.json`** antes de reinstalar el
  sistema o mover la placa. Las plantillas son el “modelo” del sistema: sin
  ellas, la app arranca pero no reconoce nada.
- **Las muestras son imágenes del entorno**: pueden contener información
  sensible del fondo. Tratalas como datos privados.
- **No modifiques la configuración de routers** para “hacer andar” el
  despliegue. El descubrimiento es no invasivo a propósito.
- **`deploy.sh` usa `sshpass` solo si se lo pasás explícitamente**; la
  contraseña no se guarda en el repositorio. Preferí claves SSH.
- **Los scripts validan y escapan** los parámetros que interpolan en comandos
  remotos. No los edites para aceptar valores arbitrarios.

---

## Estado actual verificado

| Ítem | Estado |
|---|---|
| Código fuente | Completo (`app/`, `web/`, `scripts/`, `systemd/`, `tests/`) |
| Auditoría estática | **APROBADA — 0 incidencias abiertas** |
| Incidencias del ciclo 1 | 4/4 resueltas (label NPZ, frame sintético, body de reload, inyección en deploy) |
| Tests | **41 passed** en el notebook (5.32 s) |
| Arranque local | Verificado con `scripts/run_local.sh` |
| Modo enseñar | Implementado y cubierto por tests de API |
| Despliegue en la UNO Q | **Realizado**: systemd `detector-tarjetas.service`, app en `192.168.1.5:8000` (ver `DESPLIEGUE_UNOQ.md`) |
| Candidato de red | `192.168.1.4` (MAC `2a:71:1b:c4:cd:e7`): responde ping/ARP pero **sin ningún puerto TCP abierto** |
| SSH / mDNS | No detectados en la LAN; `avahi` no instalado |
| Próximo paso | Conectar por USB-C y usar `--via-adb` o habilitar SSH desde App Lab |

La red observada durante el desarrollo (documentada solo como referencia, **sin
modificarla**): notebook `192.168.1.10/24` por Wi-Fi, gateway `192.168.1.254`,
router `192.168.1.3` con 80/443 (probable Teltonika), candidato UNO Q
`192.168.1.4` sin puertos abiertos y `192.168.1.5` (Samsung). Las subredes
típicas de Teltonika (`192.168.2.1`, `192.168.0.1`, `10.0.0.1`, `192.168.88.1`)
no eran alcanzables.

---

## Estructura del repositorio

```
detector-tarjetas/
├── app/
│   ├── __init__.py          versión y docstring del paquete
│   ├── config.py            constantes, rutas y variables de entorno
│   ├── camera.py            hilo V4L2, reconexión, frame de estado
│   ├── detector.py          ORB, matching, homografía, NMS, clustering, NPZ
│   ├── teach.py             captura/borrado de muestras
│   ├── specs.py             carga y validación de boards.json
│   └── server.py            HTTP, rutas, stream MJPEG, señales, main()
├── web/
│   ├── index.html           página del detector
│   ├── teach.html           página del modo enseñar
│   └── static/
│       ├── app.js           polling, canvas, globos, panel lateral
│       ├── teach.js         captura, recarga, borrado, conteos
│       └── style.css        tema cómic oscuro, sin CDNs
├── data/
│   ├── boards.json          fichas técnicas (fuente de verdad de la UI)
│   └── templates/           plantillas ORB .npz (runtime; solo .gitkeep)
├── scripts/
│   ├── discover_unoq.sh     descubrimiento no invasivo en la LAN
│   ├── deploy.sh            despliegue SSH/scp + systemd (+ fallback ADB)
│   └── run_local.sh         arranque local con venv
├── systemd/
│   └── detector-tarjetas.service
├── tests/
│   ├── helpers.py           texturas sintéticas, FakeCamera, utilidades
│   ├── test_specs.py        carga/validación de boards.json
│   ├── test_detector.py     detección, instancias, NPZ, IoU
│   └── test_api.py          endpoints, stream, teach, health
├── docs/
│   ├── datasheet.md         manual de usuario
│   ├── developer.md         documentación técnica del código
│   └── BITACORA.md          bitácora de desarrollo
├── conftest.py              raíz del proyecto en sys.path para pytest
├── requirements-dev.txt     opencv-python-headless, pytest, requests
├── .gitignore
└── README.md
```

---

## Ficha de evaluación

**Madurez global: 4.0/5**

| Criterio | Nota | Evidencia |
|---|---:|---|
| Madurez | 3/5 | 41 tests en 5,32 s, auditoría aprobada con 0 incidencias y modo enseñar cubierto por tests. El despliegue en la UNO Q quedó pendiente y la inferencia ARM64 solo está estimada en 200–300 ms. |
| Reproducibilidad | 4/5 | run_local.sh crea el venv, instala dependencias y levanta el servidor; FakeCamera permite 41 tests sin cámara. No hay prueba de integración con video ni con la placa. |
| Documentación | 4/5 | README extenso, bitácora con once problemas, datasheet y developer. La sección de estado actual declara el despliegue pendiente y la red observada sin modificarla. |
| Honestidad de límites | 5/5 | Dice explícitamente que no es un clasificador genérico, que no hace OCR, que requiere entrenamiento y que el despliegue físico no se completó. |
| Valor demostrativo | 4/5 | Implementa detección multinstancia con ORB y RANSAC, score con tres señales y UI de enseñanza en caliente, sin modelos externos ni GPU. |
| Facilidad de despliegue | 4/5 | deploy.sh cubre SSH, tar y systemd, con fallback ADB y códigos de salida claros. Requiere acceso a la placa, que en esta versión no estuvo disponible. |

Sirve para aprender detección por plantillas y para desplegar un reconocedor de cuatro placas en una LAN de confianza. La limitación que más pesa es que solo reconoce lo que se le enseña y que, en su versión inicial, el funcionamiento real en la UNO Q no llegó a verificarse.

## Lo que no funcionó

### v1.0.0 · despliegue — Placa inalcanzable por red

La UNO Q respondía ping y ARP en 192.168.1.4 pero no tenía ningún puerto TCP abierto, así que el despliegue quedó pendiente. Se documentaron tres vías: habilitar SSH desde App Lab, usar ADB por USB-C o copiar el proyecto a mano.

### v1.0.0 · serialización NPZ — Etiquetas con comillas

str() sobre un array Unicode 0-d devolvía «'arduino_uno_r3'», rompiendo el cruce con boards.json y los globos. Se agregó normalize_label() con .item(), decodificación UTF-8 y limpieza de comillas.

### v1.0.0 · modo enseñar — Muestras del frame sintético

Sin cámara, el modo enseñar aceptaba el cartel «CAMARA NO DISPONIBLE» como muestra y contaminaba las plantillas. Ahora el servidor exige camera.is_open y responde 422 sin escribir archivo.

### v1.0.0 · multinstancia — Dos placas iguales, una detectada

Con dos UNO R3 en cuadro, el ratio test descartaba la segunda porque los descriptores de la primera seguían presentes. Se repite el matching hasta cuatro veces y se eliminan del frame los keypoints dentro de la caja ya aceptada.

## Preguntas típicas

**¿Qué es Detector de tarjetas?**

Es una aplicación web que reconoce placas Arduino y Raspberry Pi con ORB y homografía RANSAC, y muestra la ficha técnica en un globo cómic sobre el video. Corre en el lado Linux de la UNO Q o en un notebook con webcam.

**¿Qué no hace?**

No es un clasificador genérico ni hace OCR de marcas o números de serie: solo detecta las etiquetas enseñadas desde /teach. Tampoco funciona sin plantillas y no tiene autenticación ni TLS.

**¿Cómo se ejecuta o despliega?**

En local se usa scripts/run_local.sh y se abre el puerto 8000; en la placa se usa scripts/deploy.sh con SSH o la variante --via-adb. El servicio systemd queda habilitado con la unidad incluida.

**¿Qué se puede reutilizar?**

El pipeline ORB con ratio test, homografía y validación geométrica es reutilizable para cualquier objeto texturado. También sirven el modo enseñar con recarga en caliente, las cajas normalizadas y la inyección de una cámara falsa para tests.

## Licencia y uso

Publicado bajo la **Licencia MIT** (archivo `LICENSE`, © 2026 wanderc137):
puedes usar, modificar, distribuir y sublicenciar el código, siempre con la
nota de copyright y de permiso incluidas.

- **OpenCV** es © sus autores y se distribuye bajo **Apache License 2.0**.
- **Arduino** y **Arduino UNO** son marcas de Arduino S.r.l.
- **Raspberry Pi** es una marca de Raspberry Pi Ltd.
- Las fichas técnicas son datos públicos de Arduino y Raspberry Pi Foundation;
  las marcas y nombres pertenecen a sus dueños.
- Las plantillas ORB que generes con `/teach` son tuyas: respaldalas y tratalas
  como datos privados.

---

<p align="center">
  <strong>Hecho con OpenCV, sin modelos externos y sin salir de la LAN.</strong><br>
  ORB + RANSAC · 100 % local · 41 tests
</p>
