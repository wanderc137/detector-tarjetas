# 🔍 Detector de tarjetas · Arduino UNO Q

**Visión artificial en tiempo real, 100 % local, sin modelos externos.**

Aplicación web que **reconoce cuatro placas electrónicas** en el video de una
cámara y dibuja sobre la imagen un **globo con la ficha técnica** de cada una.
Corre en el **lado Linux (Debian ARM64) de una Arduino UNO Q** y también en un
notebook Linux x86_64 para desarrollo. El navegador solo abre la página: la
captura, la inferencia y el servidor viven en la placa.

> **Versión:** 1.0.0 · **Catálogo:** 4 placas · **Tests:** 41/41 en el notebook
> **Despliegue real:** UNO Q por USB/adb, servicio systemd `enabled` con
> `Restart=always`. Ver [docs/ESTADO_ACTUAL.md](docs/ESTADO_ACTUAL.md).

---

## Tabla de contenidos

1. [Qué es](#qué-es)
2. [Catálogo vigente: las 4 placas](#catálogo-vigente-las-4-placas)
3. [Qué información muestra la UI](#qué-información-muestra-la-ui)
4. [Principio de funcionamiento](#principio-de-funcionamiento)
5. [Hardware, conexión y “pinout”](#hardware-conexión-y-pinout)
6. [Requisitos](#requisitos)
7. [Puesta en marcha local](#puesta-en-marcha-local)
8. [Despliegue en la Arduino UNO Q](#despliegue-en-la-arduino-uno-q)
9. [Modo enseñar: entrenar cada placa](#modo-enseñar-entrenar-cada-placa)
10. [Uso diario](#uso-diario)
11. [API HTTP](#api-http)
12. [Rendimiento, calibración y ajustes](#rendimiento-calibración-y-ajustes)
13. [Resolución de problemas](#resolución-de-problemas)
14. [Advertencias y diferencias de la UNO Q](#advertencias-y-diferencias-de-la-uno-q)
15. [Estado actual verificado](#estado-actual-verificado)
16. [Estructura del repositorio](#estructura-del-repositorio)
17. [Pruebas](#pruebas)
18. [Lecciones de campo](#lecciones-de-campo)
19. [Preguntas típicas](#preguntas-típicas)
20. [Licencia](#licencia)

---

## Qué es

`detector-tarjetas` es un **detector de objetos por plantillas visuales**. No usa
redes neuronales, no descarga pesos, no consulta servicios en la nube y no
necesita GPU. La detección se resuelve con **OpenCV ORB + homografía RANSAC** y
un conjunto de plantillas que el propio usuario enseña desde el navegador
apuntando la cámara a cada placa.

La escena se dibuja en un `<canvas>` superpuesto al stream MJPEG:

- un **marco amarillo fino y fijo** (`#FFD400`) que delimita el **área de
  escaneo** donde se debe apoyar la tarjeta; coincide con las marcas en T de la
  hoja y se ve aunque no haya detecciones;
- un **marco dorado con esquinas de visor** alrededor de la placa detectada;
- un **globo** con el nombre corto y tres datos: **MCU, RAM y reloj**;
- un **panel lateral** con la ficha completa al hacer clic en la placa o el globo.

| Aspecto | Detalle |
|---|---|
| Tipo | Aplicación web de visión en tiempo real |
| Detección | ORB + BFMatcher (Hamming) + ratio test de Lowe + homografía RANSAC |
| Catálogo | 4 placas: UNO R4 WiFi, Raspberry Pi 4, MKR WiFi 1010 y ESP32 |
| Entrada | Cámara IP HTTP/MJPEG (FFMPEG) o webcam USB UVC (`/dev/videoN`, V4L2) |
| Salida | Stream MJPEG + API JSON + canvas con marcos y globos |
| Hardware destino | Arduino UNO Q, **lado Linux Debian ARM64** |
| Alternativa | Notebook Linux x86_64 con webcam o cámara IP |
| Dependencias de runtime | OpenCV del sistema; nada de pip en la placa |
| Autenticación | No (pensada para una LAN de confianza) |

### Lo que **no** es

- No es un clasificador genérico: reconoce **solo las etiquetas enseñadas**.
- No identifica marcas, números de serie ni texto (no hace OCR).
- No es un servicio de internet: no hay TLS, usuarios ni cuentas.
- No funciona “de fábrica” sin entrenar: las fichas vienen cargadas, pero **las
  plantillas ORB se generan al enseñar cada placa**.
- **No es un sketch `.ino`**: no se compila con `arduino-cli` ni se flashea el
  STM32U585. Es una aplicación Python que corre en el Linux de la UNO Q. El lado
  MCU (STM32U585 + Zephyr) no se toca.

---

## Catálogo vigente: las 4 placas

`data/boards.json` es la fuente de verdad. El orden del archivo es el orden del
catálogo y de las fichas:

| # | Etiqueta | Placa | `short_name` |
|---|---|---|---|
| 1 | `arduino_uno_r4_wifi` | Arduino UNO R4 WiFi | `UNO R4 WiFi` |
| 2 | `raspberry_pi_4` | Raspberry Pi 4 Model B | `Pi 4` |
| 3 | `arduino_mkr_wifi_1010` | Arduino MKR WiFi 1010 | `MKR WiFi 1010` |
| 4 | `esp32` | ESP32 DevKit V1 | `ESP32` |

### Fichas técnicas

| Etiqueta | MCU / SoC | Reloj | RAM | Almacenamiento | GPIO | Voltaje | Dimensiones | Año |
|---|---|---|---|---|---|---|---|---|
| `arduino_uno_r4_wifi` | Renesas RA4M1 (Cortex-M4) + ESP32-S3 | 48 MHz | 32 KB | 256 KB | 20 | 5 V | 68.6 × 53.4 mm | 2023 |
| `raspberry_pi_4` | Broadcom BCM2711 (Cortex-A72, 4 núcleos) | 1.5 GHz | 2 GB | microSD | 40 | 5 V | 85 × 56 mm | 2019 |
| `arduino_mkr_wifi_1010` | Microchip SAMD21 (Cortex-M0+) + u-blox NINA-W102 | 48 MHz | 32 KB | 256 KB | 22 | 3.3 V | 61.5 × 25 mm | 2018 |
| `esp32` | Espressif ESP32-WROOM-32 (Xtensa LX6, 2 núcleos) | 240 MHz | 0.52 MB (532 KB en la UI) | 4 MB | 25 | 3.3 V | 51.5 × 28.5 mm | 2016 |

### Conectividad

| Placa | Conectividad |
|---|---|
| UNO R4 WiFi | Wi-Fi 802.11 b/g/n, Bluetooth 5 LE, USB-C, CAN, I2C, SPI, UART, DAC, matriz LED 12x8, Qwiic |
| Pi 4 | Wi-Fi 5, Bluetooth 5.0, Gigabit Ethernet, USB 3.0, micro-HDMI, GPIO 40 pines |
| MKR WiFi 1010 | Wi-Fi 802.11 b/g/n, Bluetooth 5.0, USB micro-B, I2C, SPI, UART, ADC, DAC, ATECC508A, Li-Po |
| ESP32 | Wi-Fi 2.4 GHz, Bluetooth 4.2, UART, I2C, SPI, ADC |

### Descripciones que muestra el panel

- **Arduino UNO R4 WiFi:** placa de 5 V con RA4M1 (Cortex-M4 a 48 MHz) y módulo
  ESP32-S3 para Wi-Fi y Bluetooth 5 LE. 32 KB de SRAM, 256 KB de flash, 20 GPIO
  (14 digitales y 6 analógicos), USB-C, CAN, I2C, SPI, UART, DAC, matriz LED 12x8
  y conector Qwiic.
- **Raspberry Pi 4 Model B:** SBC de formato credit-card con BCM2711 de cuatro
  núcleos Cortex-A72 a 1.5 GHz. Se ofrece con 1, 2, 4 u 8 GB de LPDDR4 y arranca
  desde microSD o USB. Gigabit Ethernet, Wi-Fi 5, Bluetooth 5.0, dos micro-HDMI
  4K, dos USB 3.0 y dos USB 2.0, GPIO de 40 pines y alimentación USB-C de 5 V/3 A.
- **Arduino MKR WiFi 1010:** formato MKR compacto con SAMD21 (Cortex-M0+ a 48 MHz)
  y NINA-W102 para Wi-Fi 802.11 b/g/n y Bluetooth 5.0. 32 KB de SRAM, 256 KB de
  flash, 22 pines I/O, 3.3 V, soporte Li-Po, ATECC508A, USB micro-B, I2C, SPI,
  UART, ADC y DAC.
- **ESP32 DevKit V1:** placa basada en ESP32-WROOM-32, dos núcleos Xtensa LX6 a
  240 MHz con Wi-Fi 2.4 GHz y Bluetooth 4.2. 520 KiB de SRAM (532 KB en la UI), 4 MB de flash SPI y
  ~25 GPIO con UART, I2C, SPI, ADC de 12 bits, DAC, PWM y touch. Alimentación por
  micro-USB a 3.3 V.

> **Etiquetas fuera del catálogo.** Una plantilla vieja como `raspberry_pi_2`
> puede seguir cargando y detectándose, pero **no tiene ficha**: la API devuelve
> `name` y `short_name` iguales al `label`, `summary` vacío, y el panel muestra
> “No hay una ficha técnica para esta etiqueta”. Es una degradación deliberada,
> no un error. Ver [docs/developer.md](docs/developer.md).

---

## Qué información muestra la UI

### Globo (detección en vivo)

El globo usa `short_name` y el resumen de tres claves que calcula
`BoardSpecs.get_summary()`: el MCU se corta antes del paréntesis y la RAM/reloj
se formatean en unidades legibles.

| Etiqueta | Título del globo | MCU | RAM | Reloj |
|---|---|---|---|---|
| `arduino_uno_r4_wifi` | UNO R4 WiFi | Renesas RA4M1 | 32 KB | 48 MHz |
| `raspberry_pi_4` | Pi 4 | Broadcom BCM2711 | 2 GB | 1.5 GHz |
| `arduino_mkr_wifi_1010` | MKR WiFi 1010 | Microchip SAMD21 | 32 KB | 48 MHz |
| `esp32` | ESP32 | Espressif ESP32-WROOM-32 | 532 KB | 240 MHz |

### Panel lateral (clic en la placa o el globo)

Muestra `name`, `id`, `year`, la tabla de `formatted()` y la descripción:

| Campo | UNO R4 WiFi | Pi 4 | MKR WiFi 1010 | ESP32 |
|---|---|---|---|---|
| MCU / SoC | Renesas RA4M1 (Cortex-M4) + ESP32-S3 | Broadcom BCM2711 (Cortex-A72, 4 núcleos) | Microchip SAMD21 (Cortex-M0+) + u-blox NINA-W102 | Espressif ESP32-WROOM-32 (Xtensa LX6, 2 núcleos) |
| Reloj | 48 MHz | 1.5 GHz | 48 MHz | 240 MHz |
| RAM | 32 KB | 2 GB | 32 KB | 532 KB |
| Almacenamiento | 256 KB | microSD | 256 KB | 4 MB |
| GPIO | 20 | 40 | 22 | 25 |
| Voltaje | 5 V | 5 V | 3.3 V | 3.3 V |
| Dimensiones | 68.6 × 53.4 mm | 85 × 56 mm | 61.5 × 25 mm | 51.5 × 28.5 mm |
| Año | 2023 | 2019 | 2018 | 2016 |

La conectividad se muestra como una lista separada por comas y, si la ficha
tiene `urls`, aparece el enlace “Documentación ↗” a la página oficial.

El panel se cierra solo: si la etiqueta abierta deja de aparecer en
`/api/detections` durante ~2 s (8 polls de 250 ms), la ficha se oculta y el
visor vuelve a mostrar únicamente el stream. El contador se reinicia si la
etiqueta reaparece antes del plazo, y no cuenta errores de red. También se
cierra con la ✕ o con `Esc`.

### Contrato de `/api/detections`

Cada detección lleva la etiqueta, el score, la caja normalizada y los datos de la
ficha ya resueltos:

```json
{
  "timestamp": 1763068800.123,
  "detections": [
    {
      "label": "arduino_uno_r4_wifi",
      "score": 0.912,
      "box": {"x": 0.2141, "y": 0.3056, "w": 0.4125, "h": 0.3187},
      "instance_id": 0,
      "inliers": 87,
      "name": "Arduino UNO R4 WiFi",
      "short_name": "UNO R4 WiFi",
      "summary": {"MCU": "Renesas RA4M1", "RAM": "32 KB", "Reloj": "48 MHz"}
    }
  ]
}
```

Si el `label` no está en `boards.json` (por ejemplo, `raspberry_pi_2`), el
servidor responde `name = short_name = "raspberry_pi_2"` y `summary = {}`.

---

## Principio de funcionamiento

### Pipeline completo

```
     Cámara IP (IP Webcam)                 Webcam USB UVC
     http://IP:8080/video                 /dev/videoN
              │                                 │
              │ FFMPEG                          │ V4L2 · MJPG · 640×480
              └──────────────┬──────────────────┘
                             ▼
                 ┌─────────────────────────┐
                 │  CameraCapture (hilo)   │
                 │  · abre y reintenta     │
                 │  · publica last_frame   │
                 │  · frame_queue(maxsize=2)│
                 └───────┬─────────┬───────┘
                         │         │
        last_frame ──────┘         └──────► frame_queue
             │                                    │
             ▼                                    ▼
   ┌──────────────────┐              ┌────────────────────────┐
   │ Stream MJPEG     │              │ BoardDetector (hilo)   │
   │ multipart 15 fps │              │ cada 250 ms            │
   └────────┬─────────┘              │ ORB → match → RANSAC   │
            │                        └───────────┬────────────┘
            │                                    │
            ▼                                    ▼
   ┌──────────────────────────────────────────────────────────┐
   │ Navegador: <img src="/stream.mjpg"> + <canvas>           │
   │ · polling /api/detections cada 250 ms                    │
   │ · dibuja marcos y globos estabilizados                   │
   │ · clic → panel con /api/boards                           │
   └──────────────────────────────────────────────────────────┘
```

### 1. Captura

`CameraCapture` acepta un **índice entero** (`CAMERA_INDEX`, típicamente `0`) o
una **URL** (`CAMERA_URL`). Con una URL usa el backend FFMPEG y no intenta
configurar V4L2; con un índice prueba V4L2 y cae a `CAP_ANY`, aplica MJPG,
640×480, el FPS objetivo y `BUFFERSIZE=1`. Publica el último frame y una cola de
dos elementos para el detector. Si la fuente falla, muestra un frame gris con
“CAMARA NO DISPONIBLE” y reintenta cada 2 s.

La fuente efectiva se resuelve con esta precedencia:

1. `data/camera_url.txt` (override persistido desde la barra “📷 Cámara”);
2. variable `CAMERA_URL` del entorno o de `/etc/default/detector-tarjetas`;
3. `CAMERA_INDEX`.

### 2. ORB

Cada frame se convierte a gris y se procesa con ORB (hasta **500 keypoints**,
`edgeThreshold=31`, `patchSize=31`, `fastThreshold=20`, Harris score). ORB es
rápido, binario y no necesita modelos: detecta esquinas y las describe con un
vector de 256 bits.

### 3. Matching

`BFMatcher` con distancia de Hamming y `knnMatch(k=2)`. Se conserva un par solo
si `m.distance < 0.75 * n.distance` (ratio test de Lowe). Si hay menos de **10
matches** buenos, la plantilla se descarta para ese frame.

### 4. Homografía RANSAC

Con los puntos de la plantilla como origen y los del frame como destino se estima
una homografía con `findHomography(..., RANSAC, 5.0)`. Se exigen al menos **20
inliers**. La homografía permite reconocer la placa aunque esté inclinada o
rotada, y su proyección define la caja y el centro.

### 5. Score y falsos positivos

El score combina tres señales:

```
score = 0.60 * inlier_ratio
      + 0.30 * distancia_media_normalizada
      + 0.10 * area_score
```

Se publica solo si `score ≥ 0.60`, la caja ocupa entre **2 % y 90 %** del frame,
el polígono proyectado llena al menos **20 %** de su bounding box, al menos el
**80 %** del área del quad proyectado cae **dentro del área de escaneo** y la
caja tiene al menos 8×8 px. El área de escaneo es el cuadrilátero amarillo que
se dibuja siempre sobre el video: por defecto es un **cuadrado de 226×226 px**
centrado en las puntas internas de las cuatro marcas en T de la hoja de
calibración (`SCAN_QUAD` = TL `(0.1762,0.2962)`, TR `(0.6477,0.2962)`, BR
`(0.6477,0.6499)`, BL `(0.1762,0.6499)`; configurable por env). ORB solo analiza
keypoints dentro de ese polígono —tanto del frame como de las plantillas—, y el
filtro de visibilidad (`MIN_VISIBLE_QUAD_RATIO = 0.80`) exige que el quad
proyectado caiga dentro del mismo. Así se descartan los falsos positivos de
fondo: cuando la tarjeta no está, el matcher puede reconocer la hoja/brazo
estático de las plantillas y proyectar un quad sobre las T o fuera del área.

> **La calibración asume un frame portrait 480×640** (ancho 480, alto 640):
> solo con esa orientación el quad normalizado mide 226×226 px. Las constantes
> `CAMERA_WIDTH`/`CAMERA_HEIGHT` (640×480) son **fallback/negociación**, no una
> garantía: si la fuente entrega otra resolución (por ejemplo 640×480
> landscape), el área de escaneo se deforma (~302×170 px) y deja de coincidir
> con las marcas en T. En ese caso hay que ajustar `SCAN_QUAD` (o el ROI) o
> rotar/reconfigurar la cámara para volver a portrait 480×640.

> **Plantillas y cambios del área de escaneo (I-05).** Las plantillas
> históricas se capturaron con el cuadrilátero vigente en su momento. Si cambia
> `SCAN_QUAD`, `_filter_template_scan_quad` conservará solo los keypoints de
> cada plantilla que caigan dentro del área nueva: pueden perderse keypoints
> útiles o conservarse fondo que antes quedaba dentro. Es esperable y no se
> borran plantillas automáticamente; si la detección se degrada tras un cambio
> de área, recapturá muestras nuevas con la tarjeta dentro del recuadro.
Después se aplica NMS (IoU > 0.30) y se agrupan centros a menos de 40 px.

`MAX_INSTANCES_PER_TEMPLATE = 1`: el visor muestra **una placa a la vez**. Más de
una instancia por plantilla generaba cajas fantasma por descriptores sobrantes
re-matheados; por eso se suprime la copia adicional. Sí se pueden ver **placas de
etiquetas distintas** en el mismo cuadro.

### 6. Del JSON al canvas

El navegador consulta `/api/detections` cada 250 ms. Las cajas vienen
normalizadas `[0,1]`; el canvas las escala al tamaño CSS de la imagen. El globo se
estabiliza con mediana sobre 9 frames, EMA de 0.15, zona muerta de 18 px,
histéresis de 20 px y gracia de 1500 ms para que no parpadee. Si no hay espacio
arriba, se dibuja abajo; si choca con otro globo, se desplaza.

El marco del área de escaneo se dibuja en cada `draw()` con los cuatro puntos de
`scan_quad` que expone `/api/health`, usando la misma transformación que las
cajas. Mientras la primera respuesta de health está en camino, `app.js` usa el
fallback `DEFAULT_SCAN_QUAD` con las mismas coordenadas, de modo que el marco
aparece desde el primer frame. Como se pinta antes del retorno temprano por
“sin detecciones”, permanece visible aunque la lista esté vacía; el panel se
cierra solo a los 8 polls sin la
etiqueta seleccionada (~2 s).

### ¿Por qué no usa modelos externos?

Porque el objetivo es demostrar el ciclo completo de visión clásica en una placa
de bajo consumo, sin descargas ni GPU: ORB y RANSAC son deterministas,
auditables, funcionan sin internet y el usuario puede inspeccionar cada plantilla.
El costo es que hay que enseñar cada placa y que la iluminación importa.

---

## Hardware, conexión y “pinout”

Este proyecto es **software**: no hay sensores, LEDs ni actuadores cableados al
header de la UNO Q. Por eso no existe un pinout de GPIO en el sentido tradicional.
La siguiente tabla describe **todos los recursos físicos que se usan**:

| Recurso de la UNO Q | Modo / uso | Componente conectado | Notas |
|---|---|---|---|
| USB-C de alimentación | Alimentación + datos | Fuente USB-C / notebook | También transporta `adb` para el despliegue |
| Wi-Fi o Ethernet | Red | Router de la LAN | Sirve la web en el puerto 8000 y alcanza la cámara IP |
| Header GPIO | **No se usa** | — | Si se amplía el proyecto, la lógica es **3.3 V**; no aplicar 5 V |
| Qwiic / I2C | **No se usa** | — | Libre para futuras expansiones |
| USB host (hub) | Opcional | Webcam USB UVC | Usar hub alimentado; la webcam puede desestabilizar la placa |
| Cámara IP | Entrada de video | Teléfono con app IP Webcam | `http://<IP-del-teléfono>:8080/video` |
| — | Entrada de video alternativa | Webcam USB en `/dev/videoN` | `CAMERA_INDEX=N` |

### Diagrama de conexión

```
        ┌──────────────────────────────┐
        │  Teléfono Android            │
        │  app «IP Webcam»             │
        │  http://192.168.1.7:8080/video│
        └──────────────┬───────────────┘
                       │ Wi-Fi de la LAN
                       ▼
        ┌──────────────────────────────┐        ┌─────────────────────┐
        │  Router / switch             │◄──────►│  Notebook / tablet  │
        │  DHCP + HTTP                 │  HTTP  │  navegador          │
        └──────────────┬───────────────┘        │  http://UNOQ:8000/  │
                       │ Wi-Fi / Ethernet        └─────────────────────┘
                       ▼
        ┌──────────────────────────────┐
        │  Arduino UNO Q               │
        │  lado Linux Debian ARM64     │
        │  · app.server :8000          │
        │  · CameraCapture (FFMPEG)    │
        │  · BoardDetector (ORB)       │
        │  · systemd: detector-tarjetas│
        └──────────────┬───────────────┘
                       │ USB-C
                       ▼
        ┌──────────────────────────────┐
        │  Fuente 5 V / notebook       │
        │  (y adb para desplegar)      │
        └──────────────────────────────┘

  Opcional: webcam USB UVC → hub USB-C alimentado → UNO Q
            (CAMERA_INDEX=0 en lugar de CAMERA_URL)
```

### Alimentación y puertos

- La UNO Q se alimenta por **USB-C**. Para una demo estable, usá una fuente con
  corriente suficiente; si agregás un hub y una webcam, el hub debe ser
  alimentado.
- La cámara IP no consume corriente de la placa: solo red.
- El puerto **8000/TCP** debe estar accesible desde el navegador. No abras ese
  puerto a internet.
- El lado MCU (STM32U585) y su firmware Zephyr **no se usan** en esta versión.

---

## Requisitos

### Hardware

| Elemento | Recomendación |
|---|---|
| Arduino UNO Q | Lado Linux con Debian ARM64 y Python 3 |
| Cámara IP | Teléfono con IP Webcam, misma LAN; o webcam USB UVC |
| Red | Router con DHCP; IP fija o reserva para la placa si es posible |
| Alimentación | USB-C de 5 V con corriente suficiente |
| Cliente | Notebook, tablet o teléfono con navegador moderno |

### Software

| Entorno | Dependencias |
|---|---|
| UNO Q (producción) | `python3`, `python3-opencv`, `v4l-utils`; `systemd`; `adb` solo en el host que despliega |
| Notebook (desarrollo) | Python 3.10+, `requirements-dev.txt` (`opencv-python-headless`, `pytest`, `requests`) |

En la UNO Q **no se usa pip**: se instala `python3-opencv` del sistema con `apt`.
El despliegue lo verifica e instala si falta (con `sudo` sin contraseña).

### Verificar la cámara

```bash
# Webcam local
v4l2-ctl --list-devices

# Cámara IP: debe devolver un JPEG o un stream MJPEG
curl -I http://192.168.1.7:8080/video
```

---

## Puesta en marcha local

### Opción A — script todo-en-uno

```bash
./scripts/run_local.sh
```

Crea `.venv`, instala `requirements-dev.txt`, exporta `PORT=8000`,
`CAMERA_INDEX=0`, `HOST=0.0.0.0` y arranca `python -m app.server`.

### Opción B — manual

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python3 -m app.server
```

Abrir `http://localhost:8000/`. Para usar una cámara IP en local:

```bash
CAMERA_URL=http://192.168.1.7:8080/video python3 -m app.server
```

### Verificar que está vivo

```bash
curl -s http://localhost:8000/api/health | python3 -m json.tool
curl -s http://localhost:8000/api/boards | python3 -m json.tool
```

La primera respuesta debe incluir `"status": "ok"` y
`"camera": {"present": true, ...}` cuando la fuente entrega frames.

---

## Despliegue en la Arduino UNO Q

### Paso 1 — Descubrir la placa (si usás red)

```bash
./scripts/discover_unoq.sh
# o acotando el rango:
./scripts/discover_unoq.sh --subnet 192.168.1.0/24
```

El script hace ping, prueba los puertos 22/80/443/8000/8080 y consulta
`/api/health` en el puerto 8000. No modifica la red.

### Paso 2 — Desplegar por USB/adb (vía recomendada en campo)

Con la UNO Q conectada por USB-C y la depuración aceptada:

```bash
adb devices
# Debe listar el dispositivo, por ejemplo: 142970805   device

./scripts/deploy.sh --via-adb --adb-serial 142970805
```

`deploy.sh` hace todo el ciclo:

1. valida el serial y comprueba que `adb` exista;
2. empaqueta el proyecto con `tar` (excluye `.venv`, `.git`, `PLAN.md`,
   `AUDIT_REPORT.md` y `data/templates/*` para **no pisar las plantillas**);
3. lo copia a la placa y crea `data/templates/`;
4. verifica `python3-opencv` y `v4l-utils`, e instala con `apt` si falta;
5. renderiza `systemd/detector-tarjetas.service` con usuario, grupo, directorio y
   puerto reales;
6. siembra `/etc/default/detector-tarjetas` **solo si no existe** (nunca
   sobrescribe `CAMERA_URL`/`CAMERA_INDEX`);
7. ejecuta `systemctl daemon-reload`, `enable` y `restart`;
8. verifica `systemctl is-enabled` (`enabled`), `systemctl is-active` (`active`) y
   `/api/health`.

Si `adb` no está instalado en el host, el script lo informa y sugiere las
alternativas (instalarlo con autorización, usar App Lab o habilitar SSH). No
instala nada por su cuenta.

### Paso 3 — Alternativa por SSH

Si SSH está habilitado en la placa:

```bash
./scripts/deploy.sh --host 192.168.1.4 --user arduino --port 2222
```

También admite `UNOQ_HOST`, `UNOQ_USER`, `UNOQ_PORT`, `UNOQ_PASSWORD` (requiere
`sshpass`, nunca se guarda en el repo), `UNOQ_APP_PORT` y `UNOQ_REMOTE_DIR`.
El script valida host, puerto, usuario, grupo, serial y rutas antes de ejecutar
nada remoto.

### Servicio systemd

La unidad es una **plantilla** (`systemd/detector-tarjetas.service`) con
`@USER@`, `@GROUP@`, `@WORKDIR@` y `@PORT@`. El servicio:

- arranca después de `network-online.target`;
- ejecuta `/usr/bin/python3 -m app.server`;
- carga `EnvironmentFile=-/etc/default/detector-tarjetas`;
- tiene `Restart=always` y `RestartSec=3`;
- pertenece al grupo `video` (`SupplementaryGroups=video`);
- aplica `NoNewPrivileges=true` y `PrivateTmp=true`.

Comandos de operación en la placa:

```bash
systemctl is-enabled detector-tarjetas    # enabled
systemctl is-active detector-tarjetas     # active
systemctl status detector-tarjetas
sudo systemctl restart detector-tarjetas
journalctl -u detector-tarjetas -f
```

Con `Restart=always`, si el proceso muere por una cámara desconectada o un error
transitorio, systemd lo vuelve a levantar a los 3 s. El estado `enabled` hace que
arranque solo al energizar la placa.

### Configuración de cámara en la placa

El archivo `/etc/default/detector-tarjetas` se carga como `EnvironmentFile` y
**no se sobrescribe** en un redeploy:

```ini
# /etc/default/detector-tarjetas
# CAMERA_URL tiene prioridad sobre CAMERA_INDEX cuando está definida.
CAMERA_URL=http://192.168.1.7:8080/video
CAMERA_INDEX=0
PORT=8000
LOG_LEVEL=INFO
```

Después de editarlo:

```bash
sudo systemctl restart detector-tarjetas
```

También podés cambiar la fuente **en caliente** desde la barra “📷 Cámara” de la
página. Esa acción hace `POST /api/camera`, valida que la URL sea `http`, `https`
o `rtsp`, la aplica sin reiniciar y la persiste en `data/camera_url.txt`. Esa
persistencia tiene prioridad sobre `CAMERA_URL`; si querés volver a la variable
de entorno, borrá `data/camera_url.txt` y reiniciá el servicio.

### Verificación posterior

```bash
# En la placa o desde la LAN
curl -s http://127.0.0.1:8000/api/health | python3 -m json.tool
curl -s http://127.0.0.1:8000/api/boards | python3 -m json.tool

# Stream en el navegador
# http://<ip-unoq>:8000/
```

| Comprobación | Resultado esperado |
|---|---|
| `systemctl is-enabled detector-tarjetas` | `enabled` |
| `systemctl is-active detector-tarjetas` | `active` |
| `/api/health` | `"status": "ok"` y `camera.present` coherente |
| `/api/boards` | 4 fichas con `formatted` y `summary` |
| `/` | Página del detector y stream en vivo |

---

## Modo enseñar: entrenar cada placa

El modo enseñar vive en `http://<ip>:8000/teach`. **No hay plantillas
preentrenadas para las 4 placas del catálogo**: hay que capturarlas. El detector
solo reconoce lo que se le enseña.

### Procedimiento

1. Abrir `/teach`.
2. Elegir la etiqueta en el desplegable:
   - `arduino_uno_r4_wifi` → Arduino UNO R4 WiFi
   - `raspberry_pi_4` → Raspberry Pi 4 Model B
   - `arduino_mkr_wifi_1010` → Arduino MKR WiFi 1010
   - `esp32` → ESP32 DevKit V1
3. Encuadrar la placa con luz difusa y fondo contrastante.
4. Capturar **10–20 muestras** por placa, variando ángulo (±30–40°), distancia
   (20–60 cm) y posición en el encuadre.
5. Verificar el conteo en “Muestras guardadas”.
6. Volver al detector y comprobar que aparece el globo.

Cada captura:

- exige que la cámara esté entregando **frames reales** (no guarda el frame gris
  de estado);
- exige al menos **50 keypoints**;
- guarda un `.npz` comprimido en `data/templates/<label>/` con los descriptores,
  puntos, tamaños, ángulos, respuestas, octavas y el tamaño de imagen;
- recarga el detector en caliente.

### Reglas prácticas

| Situación | Acción |
|---|---|
| La placa no se detecta | Agregar muestras desde ángulos y distancias nuevas |
| Se detecta el fondo | Recapturar con fondo liso y mate; subir `SCORE_THRESHOLD` |
| Se confunde con otra placa | Añadir muestras discriminativas (conectores, serigrafía) |
| Detección inestable | Mejorar luz, evitar reflejos, acercar la cámara |
| Cambió la cámara o la luz | Reentrenar las 4 etiquetas |
| Cambió el área de escaneo (`SCAN_QUAD`) | Las plantillas históricas pueden requerir recaptura con la tarjeta dentro del recuadro nuevo; no borrar muestras sin respaldar `data/templates/` |

### Borrado

| Botón | Efecto |
|---|---|
| Borrar muestras de esta etiqueta | Elimina `data/templates/<label>/*.npz` |
| Borrar todas | Envía `__all__` y elimina todas las muestras |

El borrado es permanente y el detector se recarga al terminar. Respaldá
`data/templates/` antes de limpiar.

### Scripts opcionales

- `scripts/seed_templates.py`: siembra plantillas desde imágenes.
- `scripts/train_from_stream.py`: captura muestras desde el stream.
- `scripts/diag_match.py`: diagnostica matches e inliers de una imagen.

Las plantillas sembradas desde imágenes de catálogo pueden incluir el fondo y
generar falsos positivos; para producción conviene capturar con la cámara real.

---

## Uso diario

1. Encender la UNO Q (el servicio arranca solo si está `enabled`).
2. Abrir `http://<ip-unoq>:8000/` desde un navegador de la LAN.
3. Apuntar la cámara a una placa entrenada.
4. Esperar el marco dorado y el globo (≤ 1 s en condiciones normales).
5. Hacer clic en la placa o el globo para abrir la ficha completa.
6. Cerrar el panel con `×` o `Esc`.

### Señales de la interfaz

| Señal | Significado |
|---|---|
| Píldora “en vivo · N fps” | Stream activo y FPS medido |
| “sin cámara” | La fuente no entrega frames; revisar `CAMERA_URL`/`CAMERA_INDEX` |
| Franja amarilla | `camera.present=false` |
| Franja azul | No hay ninguna muestra guardada |
| Marco dorado fino | Detección con score suficiente |
| Marco más grueso | Placa seleccionada (ficha abierta) |
| Globo arriba/abajo | Se ubica donde haya espacio y no choque con otro globo |
| Sin globo | Sin plantilla, score bajo o placa fuera de encuadre |

### Atajos

| Tecla / acción | Efecto |
|---|---|
| `Esc` | Cierra el panel lateral |
| Clic en el globo | Abre la ficha de esa placa |
| “🎓 Modo enseñar” | Va a `/teach` |
| “← Volver al detector” | Vuelve a `/` |

---

## API HTTP

Todo se sirve en el mismo puerto (8000 por defecto), sin autenticación y con
CORS `*`. Las respuestas JSON usan `Cache-Control: no-store`; los estáticos
también se sirven sin caché para que un redeploy se vea al recargar.

| Método | Ruta | Descripción | Códigos |
|---|---|---|---|
| GET | `/` · `/index.html` | Página principal | 200 |
| GET | `/teach` · `/teach/` · `/teach.html` | Modo enseñar | 200 |
| GET | `/static/*` | CSS/JS/imágenes con protección de *path traversal* | 200, 403, 404 |
| GET | `/stream.mjpg` | Stream MJPEG `multipart/x-mixed-replace` | 200 |
| GET | `/api/detections` | Detecciones + ficha resuelta | 200 |
| GET | `/api/boards` | Fichas completas + `formatted` + `summary` | 200, 500 |
| GET | `/api/health` | Cámara, plantillas, detector, uptime | 200 |
| GET | `/api/teach/samples` | Conteo de muestras por etiqueta | 200 |
| GET | `/favicon.ico` | Sin contenido | 204 |
| POST | `/api/teach/capture` | Captura una muestra | 200, 400, 422, 500 |
| POST | `/api/teach/reset` | Borra una etiqueta o `__all__` | 200, 400 |
| POST | `/api/teach/reload` | Recarga plantillas (body opcional) | 200, 400 |
| POST | `/api/camera` | Cambia y persiste la URL de cámara | 200, 400, 500 |
| OPTIONS | cualquiera | Preflight CORS | 204 |
| GET | ruta desconocida | Error JSON | 404 |

### Ejemplos con `curl`

```bash
BASE=http://localhost:8000

curl -s "$BASE/api/health" | python3 -m json.tool
curl -s "$BASE/api/detections" | python3 -m json.tool
curl -s "$BASE/api/boards" | python3 -m json.tool
curl -s "$BASE/api/teach/samples" | python3 -m json.tool

# Capturar una muestra para Arduino UNO R4 WiFi
curl -s -X POST "$BASE/api/teach/capture" \
     -H 'Content-Type: application/json' \
     -d '{"label":"arduino_uno_r4_wifi"}' | python3 -m json.tool

# Cambiar la cámara en caliente
curl -s -X POST "$BASE/api/camera" \
     -H 'Content-Type: application/json' \
     -d '{"url":"http://192.168.1.7:8080/video"}' | python3 -m json.tool

# Recargar plantillas
curl -s -X POST "$BASE/api/teach/reload" | python3 -m json.tool

# Borrar una etiqueta o todas
curl -s -X POST "$BASE/api/teach/reset" \
     -H 'Content-Type: application/json' \
     -d '{"label":"arduino_uno_r4_wifi"}' | python3 -m json.tool
curl -s -X POST "$BASE/api/teach/reset" \
     -H 'Content-Type: application/json' \
     -d '{"label":"__all__"}' | python3 -m json.tool
```

### `GET /api/health` (ejemplo)

```json
{
  "status": "ok",
  "camera": {
    "present": true,
    "resolution": [640, 480],
    "fps": 14.7,
    "index": "http://192.168.1.7:8080/video"
  },
  "templates": {
    "count": 12,
    "labels": ["arduino_uno_r4_wifi", "raspberry_pi_4"],
    "by_label": {
      "arduino_uno_r4_wifi": 8,
      "raspberry_pi_4": 4,
      "arduino_mkr_wifi_1010": 0,
      "esp32": 0
    },
    "loaded": {"arduino_uno_r4_wifi": 8, "raspberry_pi_4": 4}
  },
  "detector": {"last_inference_ms": 248.3, "inferences": 1532},
  "uptime_s": 421.7,
  "version": "1.0.0"
}
```

`camera.index` es la fuente efectiva: puede ser una URL o un índice. `by_label`
incluye las cuatro etiquetas conocidas aunque estén en 0; `labels` solo lista las
que tienen muestras y `loaded` lo que el detector tiene en memoria.

### Errores de teach

| Situación | Código | `message` |
|---|---|---|
| Etiqueta inválida | 422 | `Etiqueta inválida: usá entre 2 y 40 caracteres [a-z0-9_] ...` |
| Cámara no entrega imágenes | 422 | `La cámara no está entregando imágenes reales...` |
| Menos de 50 keypoints | 422 | `Pocos puntos característicos (N/50)...` |
| Body ausente/inválido/grande | 400 | `Falta el header Content-Length` / `JSON inválido` / ... |

---

## Rendimiento, calibración y ajustes

### Valores esperados

| Métrica | Valor |
|---|---|
| Resolución de captura | 640 × 480 |
| Formato local | MJPG (V4L2) |
| FPS de cámara objetivo | 15 |
| FPS de stream MJPEG | hasta 15 (`STREAM_MAX_FPS`) |
| Calidad JPEG | 85 |
| Intervalo entre inferencias | mínimo 250 ms (≈4 Hz) |
| Inferencia objetivo en ARM64 | 200–300 ms |
| Ancho de banda aproximado | 1–3 Mbps a 640×480 y ~12 fps |

La inferencia y el stream son independientes: aunque el detector tarde 300 ms,
el video sigue a 15 fps porque el stream lee `last_frame` sin esperar al detector.

### Variables de entorno

| Variable | Default | Uso |
|---|---|---|
| `CAMERA_URL` | vacío | URL de cámara IP (`http`, `https`, `rtsp`) |
| `CAMERA_INDEX` | `0` | Índice de `/dev/videoN` |
| `CAMERA_URL_FILE` | `data/camera_url.txt` | Override persistido desde la web |
| `PORT` | `8000` | Puerto HTTP |
| `HOST` | `0.0.0.0` | Interfaz de escucha |
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
| `MIN_INLIERS` | `20` | Mínimo de inliers RANSAC |
| `RANSAC_REPROJ_THRESHOLD` | `5.0` px | Tolerancia de reproyección |
| `SCORE_THRESHOLD` | `0.60` | Umbral de publicación |
| `MIN_BOX_AREA_RATIO` | `0.02` | Caja mínima (2 % del frame) |
| `MAX_BOX_AREA_RATIO` | `0.90` | Caja máxima (90 % del frame) |
| `NMS_IOU_THRESHOLD` | `0.30` | Solapamiento para suprimir |
| `INSTANCE_CENTER_DISTANCE_PX` | `40` | Agrupación de instancias |
| `MAX_INSTANCES_PER_TEMPLATE` | `1` | Una detección por plantilla |
| `MIN_POLYGON_FILL_RATIO` | `0.20` | Descarte de homografías degeneradas |
| `TEMPLATE_MIN_KEYPOINTS` | `50` | Mínimo para aceptar una muestra |
| `JPEG_QUALITY` | `85` | Calidad del stream |
| `STREAM_MAX_FPS` | `15` | Techo del stream |

### Calibración visual

No hay calibración de sensores. La “calibración” es visual y se hace con tres
palancas:

1. **Muestras**: 10–20 por placa, con fondo limpio y variaciones reales.
2. **Umbrales**: si hay falsos positivos, subir `SCORE_THRESHOLD` (0.60 → 0.65) o
   `MIN_INLIERS` (20 → 25); si no detecta, bajarlos de a poco.
3. **Cámara**: luz difusa, sin reflejos, distancia 20–60 cm y encuadre estable.

Si la UNO Q va justa de CPU, bajá `ORB_FEATURES` a 300–400 o subí
`DETECTOR_INTERVAL_MS` a 400 ms. Si el stream se corta, bajá `STREAM_MAX_FPS`.

---

## Resolución de problemas

| Síntoma | Causa probable | Solución |
|---|---|---|
| `camera.present=false` | URL/IP cambiada, cámara apagada o índice incorrecto | Verificar `CAMERA_URL`/`CAMERA_INDEX`; probar `/video` en el navegador |
| La cámara IP no abre | DHCP le dio otra IP al teléfono | Reservar IP o actualizar la URL desde “📷 Cámara” |
| “CAMARA NO DISPONIBLE” | Fuente caída | Revisar red/cable; el servicio reintenta cada 2 s |
| No detecta nada | No hay plantillas o score bajo | Entrenar en `/teach`; revisar `templates.loaded` |
| Detecta el fondo | Plantillas con mesa/fondo | Recapturar con fondo liso; subir umbrales |
| Globo inestable | Detección ruidosa | Mejor luz, más muestras; el suavizado ya aplica mediana/EMA |
| `systemctl is-active` = failed | OpenCV ausente, puerto ocupado o error de arranque | `journalctl -u detector-tarjetas -n 100` |
| `adb devices` vacío | Cable solo-carga, depuración no aceptada | Probar otro cable USB-C, aceptar el diálogo, revisar `adb kill-server` |
| `deploy.sh` sale con 3 | `sudo` pide contraseña o falta OpenCV | Instalar `python3-opencv v4l-utils` a mano y repetir con `--skip-apt` |
| `deploy.sh` sale con 6 | La unidad instalada tiene otro usuario/directorio | Usar los mismos valores o `--force-unit` |
| El puerto 8000 no responde | Servicio caído o firewall | `ss -ltnp \| grep 8000`; revisar firewall de la LAN |
| Webcam desestabiliza la placa | Consumo del hub USB | Usar hub alimentado o pasar a cámara IP |

### Diagnóstico rápido

```bash
# ¿Está el proceso?
systemctl status detector-tarjetas --no-pager

# ¿Qué dice el servicio?
journalctl -u detector-tarjetas -n 100 --no-pager

# ¿La cámara existe y quién la usa?
v4l2-ctl --list-devices
fuser -v /dev/video0

# ¿La API responde?
curl -s http://127.0.0.1:8000/api/health | python3 -m json.tool

# ¿Qué plantillas hay?
find data/templates -name '*.npz' | sort
```

---

## Advertencias y diferencias de la UNO Q

> ⚠️ **No es un Arduino clásico.** Esta aplicación corre en el **lado Linux
> Debian ARM64** de la UNO Q, no en el microcontrolador. No hay `setup()`/`loop()`,
> no se compila con `arduino-cli` y no se flashea el STM32U585. El lado MCU
> (Cortex-M33 con Zephyr) queda libre para otro firmware.

> ⚠️ **Lógica a 3.3 V.** El header GPIO de la UNO Q es de **3.3 V**. Si ampliás
> el proyecto con sensores o actuadores de 5 V, usá *level shifters*; no apliques
> 5 V directamente a los pines.

> ⚠️ **ADC de 12 bits.** Si en el futuro agregás sensores analógicos, la UNO Q
> lee **0–4095**, no 0–1023 como las placas clásicas de 10 bits. Ajustá las
> escalas en el código.

> ⚠️ **Sin autenticación ni TLS.** Cualquiera en la LAN puede ver el video,
> capturar muestras o borrar plantillas. No expongas el puerto 8000 a internet.

> ⚠️ **Privacidad.** El stream muestra lo que apunte la cámara. No enfoques
> personas, documentos ni información sensible.

> ⚠️ **Alimentación.** Una webcam USB que consume más corriente de la disponible
> provoca desconexiones. Usá un hub USB-C alimentado; el despliegue real usa
> cámara IP por Wi-Fi.

> ⚠️ **Plantillas = modelo.** Si se pierden, hay que reentrenar. Respaldá
> `data/templates/` y `data/boards.json`.

> ⚠️ **`boards.json` es código de datos.** Un JSON inválido o un campo faltante
> hace que `/api/boards` devuelva 500. Validá antes de reiniciar.

> ⚠️ **Sin modelos externos.** No descargues pesos ni datasets: rompen la premisa
> de operación sin internet y no están soportados por el código.

> ⚠️ **El despliegue por USB requiere `adb`.** Si `adb devices` no ve la placa,
> no hay nada que subir: revisá cable, permisos udev y la autorización de
> depuración en la UNO Q.

### Limitaciones técnicas

- ORB no es invariante a cambios extremos de escala (rango útil ≈0.6×–1.6×).
- Placas visualmente muy similares pueden confundirse si las muestras no son
  discriminativas.
- Oclusiones grandes (una mano sobre el microcontrolador) reducen los inliers.
- El rendimiento depende de la CPU de la UNO Q; la inferencia objetivo es
  200–300 ms.
- El stream MJPEG consume ancho de banda proporcional a resolución y FPS.

---

## Estado actual verificado

| Aspecto | Estado |
|---|---|
| Catálogo | **4 fichas** en `data/boards.json` (orden: UNO R4 WiFi, Pi 4, MKR WiFi 1010, ESP32) |
| Servicio en la UNO Q | `detector-tarjetas` **enabled** y **active**, `Restart=always` |
| Despliegue realizado | USB/adb, serial **142970805** |
| Cámara de campo | Teléfono Android con IP Webcam por Wi-Fi; URL editable desde la web |
| Plantillas del catálogo | **0 muestras** al cierre de esta edición: hay que entrenar las 4 |
| Plantilla histórica | `raspberry_pi_2`, 37 muestras, **label degradado fuera del catálogo** |
| Umbrales vigentes | score 0.60, 20 inliers, 1 instancia por plantilla |
| Tests | 41/41 en notebook (pytest) |

El detector es honesto sobre su estado: las 4 fichas están completas, pero el
reconocimiento real depende de capturar muestras con la cámara definitiva. La
etiqueta `raspberry_pi_2` sigue funcionando como etiqueta sin ficha para no
romper las plantillas históricas.

---

## Estructura del repositorio

```
detector-tarjetas/
├── app/
│   ├── __init__.py          # versión
│   ├── config.py            # rutas, cámara, umbrales, etiquetas
│   ├── camera.py            # captura V4L2/FFMPEG en un hilo
│   ├── detector.py          # ORB + matching + RANSAC + NMS
│   ├── specs.py             # carga y formatea boards.json
│   ├── teach.py             # captura/borrado de plantillas
│   └── server.py            # HTTP, API, stream MJPEG, UI
├── web/
│   ├── index.html           # detector
│   ├── teach.html           # modo enseñar
│   └── static/              # app.js, teach.js, style.css
├── data/
│   ├── boards.json          # 4 fichas (fuente de verdad)
│   └── templates/           # .npz por etiqueta (no se versionan)
├── scripts/
│   ├── run_local.sh
│   ├── discover_unoq.sh
│   ├── deploy.sh            # SSH o --via-adb
│   ├── seed_templates.py
│   ├── train_from_stream.py
│   └── diag_match.py
├── systemd/
│   ├── detector-tarjetas.service
│   └── detector-tarjetas.env.example
├── tests/                   # 41 pruebas
├── docs/
│   ├── datasheet.md
│   ├── developer.md
│   ├── ESTADO_ACTUAL.md
│   ├── BITACORA.md
│   └── index.html
├── README.md
├── DESPLIEGUE_UNOQ.md
├── requirements-dev.txt
└── LICENSE
```

---

## Pruebas

```bash
# Todo
python3 -m pytest -q

# Solo detector
python3 -m pytest tests/test_detector.py -q

# Un test concreto
python3 -m pytest tests/test_api.py::test_health -q

# Con salida detallada
python3 -m pytest -vv
```

Las pruebas usan imágenes sintéticas y un `ServerHarness`; no necesitan cámara ni
hardware. Resultado de referencia: **41 passed**.

---

## Lecciones de campo

Estas son las decisiones que cambiaron el proyecto después de probarlo en la UNO
Q real (el detalle histórico está en [docs/BITACORA.md](docs/BITACORA.md)):

- **La webcam USB por el hub desestabilizaba la placa.** Se pasó a cámara IP y se
  agregó `CAMERA_URL` editable desde la página, persistida porque la IP del
  teléfono cambia por DHCP.
- **Las plantillas sembradas desde imágenes de catálogo generaban falsos
  positivos.** Se borraron y se capturaron muestras reales; además se subieron
  `SCORE_THRESHOLD` a 0.60 y `MIN_INLIERS` a 20.
- **El globo tenía jitter.** Se aplicó mediana de 9 frames, EMA 0.15, zona muerta
  de 18 px, histéresis de 20 px y gracia de 1500 ms.
- **Una instancia por plantilla.** Permitir 4 generaba cajas fantasma; el visor
  muestra una placa a la vez.
- **`raspberry_pi_2` quedó como etiqueta degradada.** Sus 37 muestras siguen
  cargando, pero no tienen ficha: es el ejemplo vivo de la degradación
  controlada.

---

## Preguntas típicas

**¿Necesito internet?**
No. Todo funciona en la LAN; solo se necesita red local para que el navegador
llegue a la placa.

**¿Usa inteligencia artificial?**
Visión por computadora clásica (ORB + RANSAC). No hay redes neuronales ni modelos
preentrenados.

**¿Por qué no detecta nada al instalar?**
Porque las plantillas se generan al enseñar. Andá a `/teach` y capturá 10–20
muestras por placa.

**¿Cuántas muestras necesito?**
Para una prueba rápida, 3–5. Para uso real, 10–20 variando ángulo, distancia y
luz.

**¿Puedo agregar otra placa?**
Sí: agregá su ficha en `data/boards.json`, su etiqueta en `DEFAULT_LABELS` y
capturá muestras en `/teach`. El detector acepta cualquier carpeta en
`data/templates/`. Ver [docs/developer.md](docs/developer.md).

**¿Funciona de noche o con poca luz?**
ORB necesita contraste y esquinas. Con poca luz baja la cantidad de keypoints y
la detección se vuelve inestable. Usá luz difusa.

**¿Detecta la placa inclinada?**
Sí, dentro de ángulos moderados (±30–40°). La homografía corrige la perspectiva.

**¿Puede distinguir dos UNO R4 WiFi iguales?**
No: se publica una instancia por plantilla porque el visor muestra una placa a la
vez. Las copias adicionales se suprimen.

**¿Puedo ver el video desde el teléfono?**
Sí, si está en la misma LAN y abrís `http://<ip-unoq>:8000/`.

**¿Se guardan las imágenes?**
El stream no se graba. Las únicas imágenes persistidas son las muestras `.npz`
(descriptores, no JPEG).

**¿Qué pasa si se cae la cámara?**
La app sigue viva, muestra un frame de estado y reintenta cada 2 s.

**¿Por qué el frame de estado dice “CAMARA NO DISPONIBLE” sin acentos?**
Porque se dibuja con las fuentes Hershey de OpenCV, que no representan acentos.
Es intencional.

---

## Licencia

MIT. Ver [LICENSE](LICENSE).

**Fin del README.** Para la ficha de usuario ver [docs/datasheet.md](docs/datasheet.md);
para la arquitectura y la API interna, [docs/developer.md](docs/developer.md).
