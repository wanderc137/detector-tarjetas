# Despliegue en Arduino UNO Q — estado real (as-built)

> Última actualización: 2026-11-19. Refleja el sistema tal como quedó funcionando
> (catálogo de 4 placas y unidad systemd renderizada desde plantilla).

## 1. Hardware

- **Arduino UNO Q** (Qualcomm QRB2210 + STM32U585), lado Linux Debian 13 (trixie), ARM64.
- **Hub USB-C 8 en 1** (MCI, SKU MCI08828): HDMI, RJ45, USB 3.0, USB 2.0, USB-C PD 87 W,
  USB-C de datos, SD/TF. Cable fijo USB-C de 15 cm.
- **Cámara elegida**: teléfono Android con la app **IP Webcam** (cámara IP por WiFi), NO la
  webcam USB. Motivo: la webcam USB por el hub dejaba la placa inestable (alimentación justa).
- Alimentación del hub: fuente de Raspberry Pi 5 en la entrada **PD**.

## 2. Modo host USB (para usar el hub como host)

La UNO Q tiene un header **JCTL** (2×5) al lado del USB-C. Para forzar modo host:

1. Apagar la placa.
2. Poner un **jumper entre `GND` y `VOL_DOWN`** del JCTL (fila próxima al letrero JCTL,
   extremo cercano al USB-C; NO usar `USB BOOT` ni `PMIC_RESET`).
3. Encender. Al arrancar, el servicio `uno-q-host.service` (instalado en la placa en
   `/home/arduino/uno-q-host/host_mode.py`) escribe `host` en
   `/sys/kernel/debug/usb/4e00000.usb/mode` y hace **parpadear el LED ROJO 3 veces**.
4. Verificar: `cat /sys/class/typec/port0/data_role` → `host [device]`.

Nota: conectada al USB-A del hub la placa queda en modo *device* (no ve cámara USB) pero
con WiFi estable. Para cámara USB por hub hace falta el modo host.

## 3. Red y accesos

| Servicio | Valor |
|---|---|
| WiFi | SSID `Nv` |
| IP de la placa | `192.168.10.112/24` (DHCP; verificada por adb en la última revisión) |
| SSH | puerto **2222**, usuario `arduino`, clave `~/.ssh/unoq_key` (en la notebook) |
| Acceso alternativo | USB-C + **adb**, serial `142970805` (la red de la notebook y la placa pueden estar en subredes distintas) |
| Web | `http://192.168.10.112:8000` (o `http://127.0.0.1:8000` desde la placa) |
| Servicio | `detector-tarjetas.service` (systemd, `enabled` + `active`, `Restart=always`, `User=jma`, `Group=jma`) |
| App en la placa | `/home/jma/detector-tarjetas` (owner `jma`, home 700 → requiere sudo) |
| Cámara (unidad) | `CAMERA_URL=http://192.168.1.8:8080/video` declarada en la unidad instalada |
| Cámara (runtime) | override persistido en `data/camera_url.txt` → `http://192.168.10.205:8080/video`; tiene prioridad sobre `CAMERA_URL` |

La contraseña sudo de `arduino` no se guarda aquí (no publicar). SSH con
`-o BatchMode=yes -i ~/.ssh/unoq_key arduino@<ip> -p 2222`.

## 4. Estado actual

- App **corriendo**, `/api/health` → `camera.present=true`, `fps ~30`,
  `index=http://192.168.10.205:8080/video`.
- **Catálogo**: `data/boards.json` tiene las **4 fichas vigentes**
  (`arduino_uno_r4_wifi`, `raspberry_pi_4`, `arduino_mkr_wifi_1010`, `esp32`) y
  `/api/boards` las devuelve en ese orden.
- **Plantillas**: quedan **37 muestras históricas** bajo la etiqueta
  `raspberry_pi_2`. Esa etiqueta ya **no está en el catálogo**: se conserva como
  *label degradado* (se carga y detecta, pero sin ficha técnica asociada). Las
  demás etiquetas están en 0. Las plantillas *sembradas* desde imágenes web se
  borraron (causaban falsos positivos).
- **Detección en vivo**: funciona (score 0.90–0.94 con la placa real). El falso positivo
  residual depende del ángulo/fondo (las muestras incluyen el fondo de la mesa; se
  sugiere recapturar con fondo limpio).
- **UI**: paleta sobria (sin neón), globo cómic sin recuadro, con suavizado y separación.

## 5. Pipeline de despliegue (desde la notebook)

```bash
# despliegue normal por SSH:
./scripts/deploy.sh --host <ip> --user arduino

# si la red no es alcanzable (subredes distintas), fallback USB-C:
./scripts/deploy.sh --via-adb --adb-serial 142970805
```

`scripts/deploy.sh`:

1. copia `app/`, `web/`, `data/boards.json` y `scripts/` con tar (preserva
   `data/templates/` y `data/camera_url.txt`);
2. **renderiza la unidad** desde `systemd/detector-tarjetas.service`
   (`@USER@`, `@GROUP@`, `@WORKDIR@`, `@PORT@`); nunca copia la plantilla cruda;
3. detecta la unidad instalada con `systemctl show -p User -p Group -p
   WorkingDirectory` y adopta esos valores si no se pasaron `--user`/`--group`/
   `--remote-dir`; si difieren del objetivo, aborta salvo `--force-unit`;
4. siembra `/etc/default/detector-tarjetas` **solo si no existe**, copiando
   `CAMERA_URL`/`CAMERA_INDEX` de la unidad anterior; un redeploy nunca
   sobrescribe la configuración local;
5. hace `daemon-reload`, `enable` y `restart`, y verifica `is-enabled`,
   `is-active` y `/api/health`.

La unidad instalada vive en `/etc/systemd/system/detector-tarjetas.service` y
carga la configuración local con
`EnvironmentFile=-/etc/default/detector-tarjetas` (ahí va `CAMERA_URL`, que tiene
prioridad sobre `CAMERA_INDEX`).

## 6. Entrenamiento (para que detecte una placa nueva)

1. Abrir `http://<ip-de-la-placa>:8000/teach`.
2. Elegir la placa del selector (carga dinámicamente las 4 fichas de `data/boards.json`).
3. Mostrar la placa real a la cámara (buena luz, fondo contrastado) y tocar
   **Capturar muestra** 4–5 veces variando ángulo/distancia.
4. Tocar **Entrenar / recargar** al final.
5. Volver a `/` y verificar el globo.

Alternativa (video→plantillas): `scripts/train_from_stream.py <label> <segundos>` genera
~100-150 plantillas desde el stream de la cámara IP con deduplicación.

## 7. Desarrollo local

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
PORT=8000 .venv/bin/python -m app.server
```

En la UNO Q se usa el OpenCV del sistema (`python3-opencv` = cv2 4.10, numpy 2.2.4).

## 8. Referencias

- Modo host: https://docs.arduino.cc/tutorials/uno-q/host-mode/
- Issue oficial: https://github.com/arduino/linux-qcom/issues/2
- Datasheet UNO Q ABX00162, sección 9.5 (JCTL).
