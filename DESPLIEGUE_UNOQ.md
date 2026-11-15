# Despliegue en Arduino UNO Q — estado real (as-built)

> Última actualización: 2026-11-14. Refleja el sistema tal como quedó funcionando.

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
| IP de la placa | `192.168.1.5/24` (estática, gateway/dns `192.168.1.254`) |
| SSH | puerto **2222**, usuario `arduino`, clave `~/.ssh/unoq_key` (en la notebook) |
| Web | `http://192.168.1.5:8000` |
| Servicio | `detector-tarjetas.service` (systemd, `enabled`, `Restart=always`, `User=jma`) |
| App en la placa | `/home/jma/detector-tarjetas` (owner `jma`, home 700 → requiere sudo) |
| Cámara | `CAMERA_URL=http://192.168.1.7:8080/video` (app IP Webcam del teléfono) |

La contraseña sudo de `arduino` no se guarda aquí (no publicar). SSH con
`-o BatchMode=yes -i ~/.ssh/unoq_key arduino@192.168.1.5 -p 2222`.

## 4. Estado actual

- App **corriendo**, `/api/health` → `camera.present=true`, `fps ~30`,
  `index=http://192.168.1.7:8080/video`.
- **Plantillas**: solo `raspberry_pi_2` tiene 24 muestras reales (capturadas por el usuario
  en `/teach`). Las demás etiquetas están en 0. Las plantillas *sembradas* desde imágenes
  web se borraron (causaban falsos positivos).
- **Detección en vivo**: funciona (score 0.90–0.94 con la placa real). El falso positivo
  residual depende del ángulo/fondo (las 24 muestras incluyen el fondo de la mesa; se
  sugiere recapturar con fondo limpio).
- **UI**: paleta sobria (sin neón), globo cómic sin recuadro, con suavizado y separación.

## 5. Pipeline de despliegue (desde la notebook)

```bash
# editar en la notebook, luego:
node --check web/static/app.js                 # si tocaste JS
scp -P 2222 -i ~/.ssh/unoq_key web/static/app.js arduino@192.168.1.5:/home/arduino/app.js

# por SSH instalar como jma y reiniciar el servicio:
printf 'CONTRASEÑA\n' | sudo -S install -m 0644 -o jma -g jma /home/arduino/app.js /home/jma/detector-tarjetas/web/static/app.js
printf 'CONTRASEÑA\n' | sudo -S systemctl restart detector-tarjetas
# esperar ~8-10s; curl transitorio HTTP 000/exit 7 es normal tras el restart
```

El servicio vive en `/etc/systemd/system/detector-tarjetas.service` con
`Environment=CAMERA_URL=...` (tiene prioridad sobre `CAMERA_INDEX`).

## 6. Entrenamiento (para que detecte una placa nueva)

1. Abrir `http://192.168.1.5:8000/teach`.
2. Elegir la placa del selector (carga dinámicamente las fichas de `data/boards.json`).
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
