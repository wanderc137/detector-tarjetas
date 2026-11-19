# Datasheet · Detector de tarjetas

**Producto:** Detector de tarjetas electrónicas por visión artificial
**Versión de software:** 1.0.0
**Plataforma:** Arduino UNO Q (Debian ARM64) · Notebook Linux x86_64 (desarrollo)
**Idioma de la interfaz:** Español
**Fecha de esta edición:** noviembre de 2026

---

## 1. Descripción general

El Detector de tarjetas es una aplicación web que **identifica placas
electrónicas frente a una cámara** y muestra sus datos técnicos en pantalla, en
tiempo real, sin enviar imágenes a internet.

La aplicación corre en el **lado Linux de la Arduino UNO Q**. El usuario abre una
página desde cualquier navegador de la misma red y ve:

- el video en vivo de la cámara (IP en el despliegue real, o webcam USB conectada a la placa);
- un **marco dorado con esquinas de visor** alrededor de cada placa reconocida;
- un **globo** con el nombre corto y tres datos (MCU, RAM y reloj);
- un **panel lateral** con la ficha técnica completa al hacer clic.

No utiliza modelos de inteligencia artificial preentrenados, ni GPU, ni servicios
en la nube. La detección se basa en **características visuales ORB** y
**homografía RANSAC**, con plantillas que el usuario enseña apuntando la cámara a
cada placa desde la propia interfaz.

### 1.1 Usuarios previstos

| Usuario | Uso típico |
|---|---|
| Docente / divulgador | Mostrar placas en una feria, clase o demo y que la pantalla “explique” qué es cada una |
| Estudiante | Aprender visión por computadora con un caso concreto y editable |
| Técnico / maker | Identificar placas a distancia y consultar sus especificaciones |
| Desarrollador | Base para agregar nuevas placas, sensores o integraciones |

### 1.2 Alcance

| Incluido | No incluido |
|---|---|
| Detección de las 4 placas del catálogo: UNO R4 WiFi, Raspberry Pi 4, MKR WiFi 1010 y ESP32 | Reconocimiento genérico de cualquier placa |
| Enseñanza de nuevas etiquetas desde el navegador | Entrenamiento de redes neuronales |
| Fichas técnicas editables en JSON | Base de datos remota o sincronización |
| Stream MJPEG y API JSON | Grabación de video o almacenamiento de imágenes |
| Despliegue por SSH y fallback ADB | Aprovisionamiento automático de la red |
| Uso en LAN | Exposición segura a internet |

---

## 2. Partes del sistema

### 2.1 Hardware

| Parte | Función | Obligatorio |
|---|---|---|
| Arduino UNO Q | Ejecuta el servidor, la captura y la inferencia (lado Linux) | Sí (o notebook) |
| Cámara IP (app IP Webcam en un teléfono) | Fuente de video por URL HTTP; es la fuente del despliegue real | Sí |
| Webcam USB UVC | Alternativa local (`CAMERA_INDEX`); por el hub puede desestabilizar la placa | No |
| Hub USB-C (alimentado) | Conecta periféricos y alimenta la placa si falta un puerto | Si hace falta |
| Router / LAN | Transporta HTTP entre la placa y el navegador | Sí |
| Notebook / PC / teléfono | Abre la interfaz web; no procesa video | Sí (cliente) |
| Fuente USB-C adecuada | Alimentación estable de la placa y el hub | Sí |

### 2.2 Software

| Componente | Dónde | Notas |
|---|---|---|
| `app/` | Placa / notebook | Servidor, cámara, detector, fichas, teach |
| `web/` | Placa / notebook | HTML, CSS y JS sin CDNs |
| `data/boards.json` | Placa / notebook | Fichas técnicas (editable) |
| `data/templates/` | Placa / notebook | Plantillas ORB (se generan al enseñar) |
| `scripts/` | Notebook | Arranque local, descubrimiento, despliegue |
| `systemd/detector-tarjetas.service` | Placa | Servicio persistente |
| `tests/` | Notebook | 41 pruebas automatizadas |

### 2.3 Interfaz de usuario

| Zona | Elemento | Función |
|---|---|---|
| Barra superior | Insignia “UNO Q” + título | Identidad |
| Barra superior | Píldora de estado | `en vivo · N fps`, `sin cámara`, `sin conexión` |
| Barra superior | Botón “🎓 Modo enseñar” | Va a `/teach` |
| Franja amarilla | Aviso de cámara | Aparece si `camera.present=false` |
| Franja azul | Aviso de plantillas | Aparece si no hay ninguna muestra |
| Escenario | Video + canvas | Stream y superposición de marcos/globos |
| Escenario | Texto de ayuda | “Hacé clic sobre una placa o su globo…” + ms de inferencia |
| Panel lateral | Ficha técnica | Se abre al hacer clic; se cierra con `×` o `Esc` |
| Pie | Leyenda y contadores | Detecciones, muestras y etiquetas entrenadas |

---

## 3. Especificaciones técnicas

### 3.1 Funcionales

| Parámetro | Especificación |
|---|---|
| Placas reconocidas de fábrica | Arduino UNO R4 WiFi, Raspberry Pi 4 Model B, Arduino MKR WiFi 1010 y ESP32 DevKit V1 (4 fichas en `data/boards.json`). Las muestras históricas de `raspberry_pi_2` siguen cargando como **label degradado**, sin ficha. |
| Método de detección | ORB (500 keypoints) + BFMatcher Hamming + ratio test 0.75 + homografía RANSAC |
| Instancias simultáneas | 1 por plantilla (el visor muestra una placa a la vez), más plantillas de otras etiquetas |
| Cajas de salida | Normalizadas `[0,1]` respecto del frame |
| Umbral de publicación | Score ≥ 0.60, ≥ 20 inliers, ≥ 10 matches |
| Entrada de video | Cámara IP HTTP/MJPEG por FFMPEG (`CAMERA_URL`) o webcam UVC por V4L2 (`CAMERA_INDEX`), 640×480, 15 fps objetivo |
| Salida de video | MJPEG `multipart/x-mixed-replace`, calidad JPEG 85, hasta 15 fps |
| Datos del globo | `short_name` + MCU (antes del paréntesis) + RAM formateada + reloj formateado |
| Datos del panel | MCU/SoC, reloj, RAM, almacenamiento, GPIO, voltaje, dimensiones, conectividad, año, descripción y enlaces |
| Frecuencia de inferencia | Mínimo 250 ms entre inferencias (≈4 Hz) |
| Latencia de superposición | ≤ 250 ms de polling + tiempo de inferencia |
| Persistencia | Archivos `.npz` en `data/templates/<etiqueta>/` |
| Recarga de plantillas | En caliente, tras cada captura o por `POST /api/teach/reload` |
| Fichas técnicas | JSON validado al cargar, recargado por `mtime`/tamaño |
| Idioma | Español (interfaz, mensajes, documentación) |

### 3.2 Eléctricas y ambientales (UNO Q + cámara)

| Parámetro | Valor |
|---|---|
| Alimentación de la UNO Q | USB-C (fuente con corriente suficiente para la placa y el hub) |
| Consumo de la webcam | Depende del modelo; usar hub alimentado si hay inestabilidad |
| Temperatura de operación | La del equipo anfitrión; evitar sol directo y encierros sin ventilación |
| Iluminación recomendada | Luz difusa, 300–800 lux sobre la placa, sin reflejos directos |
| Distancia de trabajo | 20–60 cm entre cámara y placa |
| Fondo recomendado | Superficie mate, lisa y de color contrastante |

### 3.3 De red

| Parámetro | Valor |
|---|---|
| Puerto HTTP | 8000 (configurable con `PORT`) |
| Interfaz de escucha | `0.0.0.0` (configurable con `HOST`) |
| Protocolo | HTTP/1.1 sin TLS |
| Autenticación | Ninguna |
| CORS | `Access-Control-Allow-Origin: *` |
| Descubrimiento | `scripts/discover_unoq.sh` (ping + puertos 22/80/443/8000/8080) |
| Despliegue | SSH/SCP sobre `tar` o ADB por USB-C |
| Ancho de banda estimado | 1–3 Mbps por cliente de stream a 640×480 y ~12 fps |

### 3.4 De almacenamiento

| Dato | Tamaño típico | ¿Se versiona? |
|---|---|---|
| Plantilla `.npz` (una muestra) | Decenas a cientos de KB | No (solo `.gitkeep`) |
| `boards.json` | ~4 KB para 4 placas | Sí |
| Código y web | ~150 KB | Sí |
| Logs | Según `LOG_LEVEL` y duración | No |

---

## 4. Instalación

### 4.1 Dependencias

Las dependencias del notebook están declaradas en **`requirements-dev.txt`**:

| Librería | Versión | Uso |
|---|---|---|
| `opencv-python-headless` | `>=4.8,<6` | ORB, homografía, captura, JPEG |
| `pytest` | `>=7.4` | Tests |
| `requests` | `>=2.31` | Tests de API |

En la **UNO Q no se usa pip**: se instala el paquete del sistema
`python3-opencv` y `v4l-utils` con `apt`.

### 4.2 Instalación en notebook (desarrollo)

```bash
cd detector-tarjetas
python3 -m venv venv
source venv/bin/activate        # Linux/Mac
# venv\Scripts\activate         # Windows
pip install -r requirements-dev.txt
```

O directamente:

```bash
./scripts/run_local.sh
```

El script crea `.venv`, instala `requirements-dev.txt` y arranca el servidor.

### 4.3 Instalación en la Arduino UNO Q (producción)

1. Conectar la UNO Q a la red y dejar disponible la cámara IP (o una webcam USB).
2. Descubrirla:

   ```bash
   ./scripts/discover_unoq.sh
   ```

3. Desplegar:

   ```bash
   ./scripts/deploy.sh --host <ip> --user arduino
   ```

   o, si hace falta probar credenciales por defecto:

   ```bash
   ./scripts/deploy.sh --host <ip> --user arduino --try-defaults
   ```

4. Si SSH no está disponible, usar USB-C/adb (vía de campo):

   ```bash
   adb devices
   ./scripts/deploy.sh --via-adb --adb-serial 142970805
   ```

   El script copia el proyecto, verifica OpenCV, renderiza la unidad systemd,
   siembra `/etc/default/detector-tarjetas` solo si no existe y deja el servicio
   `enabled` con `Restart=always`.

   o habilitar SSH desde Arduino App Lab:

   ```bash
   sudo systemctl enable --now ssh
   ```

5. Verificar:

   ```bash
   curl http://<ip>:8000/api/health
   ```

### 4.4 Comprobación posterior a la instalación

| Comprobación | Comando | Resultado esperado |
|---|---|---|
| Proceso vivo | `systemctl status detector-tarjetas` | `active (running)` |
| Arranque automático | `systemctl is-enabled detector-tarjetas` | `enabled` |
| API sana | `curl -s http://<ip>:8000/api/health` | `"status": "ok"` |
| Cámara presente | Igual anterior | `"present": true` |
| Stream | Abrir `/stream.mjpg` en navegador | Video en vivo |
| Plantillas | `/api/teach/samples` | Conteos por etiqueta |
| Logs | `journalctl -u detector-tarjetas -f` | Sin errores repetidos |

---

## 5. Operación

### 5.1 Arranque

| Entorno | Acción |
|---|---|
| Notebook | `./scripts/run_local.sh` y abrir `http://localhost:8000/` |
| UNO Q con systemd | El servicio arranca solo; abrir `http://<ip>:8000/` |
| UNO Q manual | `cd detector-tarjetas && python3 -m app.server` |

### 5.2 Detección

1. Apuntar la cámara a una placa entrenada.
2. Esperar a que aparezca el marco dorado (≤ 1 s en condiciones normales).
3. Leer el globo: nombre corto, MCU, RAM y reloj.
4. Hacer clic en la placa o el globo para ver la ficha completa.
5. Cerrar el panel con `×` o `Esc`.

Los valores exactos que muestra el globo para el catálogo vigente son:

| Etiqueta | Título | MCU | RAM | Reloj |
|---|---|---|---|---|
| `arduino_uno_r4_wifi` | UNO R4 WiFi | Renesas RA4M1 | 32 KB | 48 MHz |
| `raspberry_pi_4` | Pi 4 | Broadcom BCM2711 | 2 GB | 1.5 GHz |
| `arduino_mkr_wifi_1010` | MKR WiFi 1010 | Microchip SAMD21 | 32 KB | 48 MHz |
| `esp32` | ESP32 | Espressif ESP32-WROOM-32 | 532 KB | 240 MHz |

Si la etiqueta no tiene ficha (por ejemplo, la histórica `raspberry_pi_2`), el
globo muestra el `label` como título y el panel avisa que no hay ficha técnica.

### 5.3 Interpretación de las detecciones

| Señal | Significado |
|---|---|
| Marco dorado fino | Detección con score suficiente |
| Marco dorado grueso + brillo | Placa seleccionada (ficha abierta) |
| Globo arriba de la placa | Espacio libre suficiente arriba |
| Globo abajo de la placa | Se ubicó abajo por falta de espacio |
| Globo desplazado | Se movió para no pisar otro globo |
| Sin marco | No hay plantilla, score bajo, o placa fuera de encuadre |

### 5.4 Modo enseñar

1. Ir a `/teach`.
2. Elegir la etiqueta.
3. Encuadrar la placa con buena luz y fondo contrastado.
4. Capturar **10–20 muestras** variando ángulo, distancia y posición.
5. Verificar el conteo en “Muestras guardadas”.
6. Volver al detector y comprobar la detección.

> La captura ya recarga el detector. El botón “Entrenar / recargar” sirve para
> forzar la recarga si se copiaron archivos `.npz` por fuera de la interfaz.

### 5.5 Borrado de muestras

| Botón | Efecto | Confirmación |
|---|---|---|
| Borrar muestras de esta etiqueta | Elimina `data/templates/<label>/*.npz` | Sí |
| Borrar todas | Envía `__all__` y elimina todas las muestras | Sí |

El detector se recarga al finalizar. El borrado es **permanente** (no va a
papelera).

---

## 6. Mantenimiento

### 6.1 Rutinario

| Tarea | Frecuencia | Procedimiento |
|---|---|---|
| Verificar cámara | Semanal | `/api/health` con `present: true` |
| Revisar logs | Semanal | `journalctl -u detector-tarjetas -n 100` |
| Respaldar plantillas | Antes de cambios | Copiar `data/templates/` completa |
| Respaldar fichas | Antes de editar | Copiar `data/boards.json` |
| Limpiar lente | Según uso | Paño de microfibra, sin solventes |
| Reentrenar | Si cambia la luz o el lugar | Nuevas muestras desde `/teach` |

### 6.2 Respaldos

```bash
# En la UNO Q (o notebook)
tar -czf respaldo-detector-$(date +%F).tar.gz \
    data/templates data/boards.json

# Restaurar
tar -xzf respaldo-detector-2026-11-13.tar.gz
sudo systemctl restart detector-tarjetas
```

### 6.3 Actualización de software

1. Respaldar `data/templates/` y `data/boards.json`.
2. Copiar el nuevo código (sin borrar `data/templates/`).
3. Reinstalar dependencias si cambiaron.
4. `sudo systemctl restart detector-tarjetas`.
5. Verificar `/api/health` y una detección real.

> `deploy.sh` **preserva** las plantillas existentes en la placa: las excluye del
> `tar` y solo asegura que el directorio exista.

### 6.4 Cambio de cámara

1. Dejar disponible la nueva fuente (URL de la cámara IP o webcam USB conectada).
2. Si es webcam, verificar el índice: `v4l2-ctl --list-devices`.
3. Cambiar la fuente desde la barra "📷 Cámara" de la página (persiste en
   `data/camera_url.txt`) o ajustar `CAMERA_URL`/`CAMERA_INDEX` en
   `/etc/default/detector-tarjetas` y reiniciar el servicio.
4. **Reentrenar** si la nueva cámara cambia color, enfoque o distancia focal.

---

## 7. Preguntas frecuentes (FAQ)

**¿Necesito internet?**
No. La aplicación funciona completamente en la LAN. Solo se necesita red local
para que el navegador llegue a la placa.

**¿Usa inteligencia artificial?**
Usa visión por computadora clásica (ORB + RANSAC). No hay redes neuronales ni
modelos preentrenados.

**¿Por qué no detecta nada al instalar?**
Porque las plantillas se generan al enseñar. Andá a `/teach`, capturá 10–20
muestras por placa y volvé al detector.

**¿Cuántas muestras necesito?**
Para una prueba rápida, 3–5. Para uso real, 10–20 variando ángulo, distancia y
luz.

**¿Puedo agregar otra placa?**
Sí: agregá su ficha en `data/boards.json` y su etiqueta en el modo enseñar (el
código usa `DEFAULT_LABELS` para los conteos; el detector acepta cualquier
carpeta en `data/templates/`). Ver `docs/developer.md`.

**¿Funciona de noche o con poca luz?**
ORB necesita contraste y esquinas. Con poca luz baja la cantidad de keypoints y
la detección se vuelve inestable. Usá luz difusa.

**¿Detecta la placa si está inclinada?**
Sí, dentro de ángulos moderados (±30–40°). La homografía corrige la perspectiva.
Ángulos extremos o placas muy pequeñas fallan.

**¿Puede distinguir dos UNO R4 WiFi iguales?**
No: el detector publica una instancia por plantilla (`MAX_INSTANCES_PER_TEMPLATE=1`)
porque el visor muestra una placa a la vez. Las copias adicionales se suprimen.

**¿Puedo ver el video desde el teléfono?**
Sí, si el teléfono está en la misma LAN y abrís `http://<ip-unoq>:8000/`.

**¿Se guardan las imágenes?**
El stream no se graba. Las únicas imágenes persistidas son las muestras `.npz`
que se capturan en el modo enseñar (descriptores, no JPEG).

**¿Qué pasa si se cae la cámara?**
La app sigue viva, muestra un frame de estado y reintenta abrirla cada 2 s.

**¿Qué pasa si borro una plantilla?**
El detector la quita en la próxima recarga y deja de reconocer esa etiqueta.

**¿Cómo sé qué versión estoy usando?**
`/api/health` devuelve `"version": "1.0.0"`.

**¿Puedo ejecutarlo en una Raspberry Pi?**
El código es Python + OpenCV y debería funcionar en cualquier Linux ARM64/x86_64
con una cámara V4L2 o una cámara IP. El proyecto está probado en notebook y el
servicio está desplegado y activo en la UNO Q.

**¿Por qué el frame de estado dice “CAMARA NO DISPONIBLE” sin acentos?**
Porque se dibuja con las fuentes Hershey de OpenCV, que no representan acentos.
Es intencional.

---

## 8. Advertencias y límites

> ⚠️ **Seguridad:** la app no tiene autenticación ni TLS. Cualquiera en la LAN
> puede ver el video, capturar muestras o borrar plantillas. No la expongas a
> internet.

> ⚠️ **Privacidad:** el stream muestra lo que apunte la cámara. Asegurate de no
> enfocar personas, documentos ni información sensible.

> ⚠️ **Alimentación:** una webcam USB que consume más corriente de la disponible
> provoca desconexiones. Si se usa webcam, conectarla por un hub USB-C alimentado;
> el despliegue real usa cámara IP por WiFi.

> ⚠️ **Plantillas:** son el “modelo” del sistema. Si se pierden, hay que
> reentrenar. Respaldalas.

> ⚠️ **Edición de `boards.json`:** un error de sintaxis o un campo faltante hace
> que `/api/boards` devuelva 500. Validá el JSON antes de reiniciar.

> ⚠️ **No ejecutar como root:** usá el usuario del servicio y el grupo `video`.

> ⚠️ **Sin modelos externos:** no descargues pesos ni datasets; rompen la
> premisa de operación sin internet y no están soportados por el código.

### 8.1 Limitaciones técnicas

- ORB no es invariante a cambios extremos de escala; funciona bien en un rango
  moderado (≈0.6×–1.6×).
- Placas visualmente muy similares pueden confundirse si las muestras no son
  discriminativas.
- Oclusiones grandes (una mano sobre el microcontrolador) reducen los inliers.
- El rendimiento depende de la CPU de la UNO Q; la inferencia objetivo es
  200–300 ms.
- El stream MJPEG consume ancho de banda proporcional a resolución y FPS.

---

## 9. Glosario

| Término | Definición |
|---|---|
| **ORB** | Algoritmo de características visuales (FAST + BRIEF rotado) |
| **Keypoint** | Punto distintivo de la imagen (esquina, borde) |
| **Descriptor** | Vector binario de 256 bits que describe un keypoint |
| **BFMatcher** | Comparador de descriptores por fuerza bruta |
| **Hamming** | Distancia entre vectores binarios (bits distintos) |
| **Ratio test de Lowe** | Filtro que descarta matches ambiguos |
| **Homografía** | Matriz 3×3 que mapea un plano a otro |
| **RANSAC** | Método robusto para estimar un modelo con outliers |
| **Inlier** | Match consistente con la homografía estimada |
| **NMS** | Supresión de no máximos: elimina cajas solapadas |
| **IoU** | Intersección sobre unión de dos cajas |
| **MJPEG** | Stream de imágenes JPEG consecutivas |
| **UVC** | Clase USB estándar de cámaras de video |
| **V4L2** | API de video para Linux |
| **NPZ** | Archivo comprimido de NumPy con varios arrays |
| **Plantilla** | Muestra ORB guardada de una placa |

---

## 10. Soporte y referencias

| Recurso | Ubicación |
|---|---|
| README completo | `README.md` |
| Documentación de desarrollador | `docs/developer.md` |
| Bitácora de desarrollo | `docs/BITACORA.md` |
| Fichas técnicas | `data/boards.json` |
| Estado del sistema | `GET /api/health` |
| Logs del servicio | `journalctl -u detector-tarjetas` |
| Descubrimiento de red | `scripts/discover_unoq.sh` |

**Fin de la hoja de datos.** Para detalles de implementación, algoritmos,
formato de archivos y guías de extensión, consultar `docs/developer.md`.
