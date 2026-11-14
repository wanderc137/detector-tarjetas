#!/usr/bin/env bash
# Despliegue del detector de tarjetas en la Arduino UNO Q (lado Linux Debian ARM64).
#
# Copia el proyecto por SSH (tar sobre ssh, sin rsync), instala python3-opencv y
# v4l-utils si hace falta, instala la unidad systemd y verifica /api/health.
#
# Las credenciales NUNCA se hardcodean: se reciben por argumentos o variables
# de entorno.
#
# Uso:
#   ./scripts/deploy.sh --host 192.168.1.4 --user arduino
#   UNOQ_HOST=192.168.1.4 UNOQ_PASSWORD=secreto ./scripts/deploy.sh
#   ./scripts/deploy.sh --host 192.168.1.4 --user arduino --try-defaults
#   ./scripts/deploy.sh --via-adb            # fallback USB-C/adb (App Lab)
#
# Variables:
#   UNOQ_HOST, UNOQ_USER (arduino), UNOQ_PORT (22), UNOQ_REMOTE_DIR,
#   UNOQ_PASSWORD (requiere sshpass; nunca se guarda en el repo),
#   UNOQ_APP_PORT (8000), UNOQ_SERVICE (detector-tarjetas)
#
# Fallback si SSH no está habilitado: ver README.md → "Despliegue en la UNO Q".
set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="${UNOQ_SERVICE:-detector-tarjetas}"
APP_PORT="${UNOQ_APP_PORT:-8000}"
SSH_HOST="${UNOQ_HOST:-}"
SSH_USER="${UNOQ_USER:-arduino}"
SSH_PORT="${UNOQ_PORT:-22}"
PASSWORD="${UNOQ_PASSWORD:-}"
REMOTE_DIR="${UNOQ_REMOTE_DIR:-}"
SKIP_APT=0
TRY_DEFAULTS=0
VIA_ADB=0
ADB_SERIAL="${ADB_SERIAL:-}"

usage() {
  sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) SSH_HOST="${2:-}"; shift 2 ;;
    --user) SSH_USER="${2:-}"; shift 2 ;;
    --port) SSH_PORT="${2:-}"; shift 2 ;;
    --password) PASSWORD="${2:-}"; shift 2 ;;
    --remote-dir) REMOTE_DIR="${2:-}"; shift 2 ;;
    --app-port) APP_PORT="${2:-}"; shift 2 ;;
    --skip-apt) SKIP_APT=1; shift ;;
    --try-defaults) TRY_DEFAULTS=1; shift ;;
    --via-adb) VIA_ADB=1; shift ;;
    --adb-serial) ADB_SERIAL="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Opción desconocida: $1" >&2; usage >&2; exit 2 ;;
  esac
done

# ---------------------------------------------------------------------------
# Utilidades de salida y validación (I-04)
# ---------------------------------------------------------------------------
die() { echo "ERROR: $*" >&2; exit 1; }
warn() { echo "⚠️  $*" >&2; }
step() { echo; echo "==> $*"; }

# Escapa un valor para interpolarlo en un comando de shell remoto. Con las
# allowlists de abajo los valores válidos no necesitan comillas, pero se aplica
# igual como defensa en profundidad (nunca se confía en comillas simples).
quote_remote() { printf '%q' "$1"; }

validate_identifier() { # validate_identifier <nombre> <valor>
  local name="$1" value="$2"
  [[ "${value}" =~ ^[A-Za-z0-9_-]+$ ]] \
    || die "${name} inválido: '${value}'. Solo se permite [A-Za-z0-9_-]."
}

validate_port() { # validate_port <nombre> <valor>
  local name="$1" value="$2"
  [[ "${value}" =~ ^[0-9]+$ ]] \
    || die "${name} inválido: '${value}'. Debe ser un entero entre 1 y 65535."
  (( value >= 1 && value <= 65535 )) \
    || die "${name} fuera de rango: '${value}'. Debe estar entre 1 y 65535."
}

validate_remote_dir() { # validate_remote_dir <valor>
  local value="$1"
  [[ "${value}" =~ ^/[A-Za-z0-9_./-]+$ ]] \
    || die "REMOTE_DIR inválido: '${value}'. Debe ser una ruta absoluta con [A-Za-z0-9_./-]."
  [[ "${value}" != *..* ]] \
    || die "REMOTE_DIR inválido: '${value}'. No puede contener '..'."
}

validate_host() { # validate_host <valor>
  local value="$1"
  [[ "${value}" =~ ^[A-Za-z0-9_.:-]+$ ]] \
    || die "SSH_HOST inválido: '${value}'. Solo se permite hostname o IP [A-Za-z0-9_.:-]."
}

# ---------------------------------------------------------------------------
# Validación estricta de TODO lo que se interpola en comandos remotos
# ---------------------------------------------------------------------------
validate_identifier "SERVICE_NAME" "${SERVICE_NAME}"
validate_identifier "SSH_USER" "${SSH_USER}"
validate_port "SSH_PORT" "${SSH_PORT}"
validate_port "APP_PORT" "${APP_PORT}"
if [[ -n "${SSH_HOST}" ]]; then
  validate_host "${SSH_HOST}"
fi
if [[ -n "${ADB_SERIAL}" ]]; then
  [[ "${ADB_SERIAL}" =~ ^[A-Za-z0-9_.:-]+$ ]] \
    || die "ADB_SERIAL inválido: '${ADB_SERIAL}'."
fi
if [[ -z "${REMOTE_DIR}" ]]; then
  REMOTE_DIR="/home/${SSH_USER}/detector-tarjetas"
fi
validate_remote_dir "${REMOTE_DIR}"

# Valores ya validados y escapados para construir comandos ssh/scp/adb.
REMOTE_DIR_Q="$(quote_remote "${REMOTE_DIR}")"
REMOTE_TEMPLATES_Q="$(quote_remote "${REMOTE_DIR}/data/templates")"
SERVICE_NAME_Q="$(quote_remote "${SERVICE_NAME}")"
SSH_USER_Q="$(quote_remote "${SSH_USER}")"
APP_PORT_Q="$(quote_remote "${APP_PORT}")"

# ---------------------------------------------------------------------------
# Fallback por ADB (USB-C / Arduino App Lab)
# ---------------------------------------------------------------------------
deploy_via_adb() {
  step "Despliegue por ADB (USB-C)"
  if ! command -v adb >/dev/null 2>&1; then
    cat >&2 <<'MSG'
ERROR: 'adb' no está instalado en esta notebook.
Alternativas:
  1) Instalá adb con autorización del usuario: sudo apt-get install android-tools-adb
  2) Usá Arduino App Lab: abrí el proyecto, conectá la UNO Q por USB-C y copiá
     la carpeta del proyecto al lado Linux (File Manager / terminal de App Lab).
  3) Habilitá SSH en la UNO Q desde App Lab y volvé a ejecutar:
       ./scripts/deploy.sh --host <ip> --user arduino
MSG
    exit 4
  fi

  local adb_args=()
  [[ -n "${ADB_SERIAL}" ]] && adb_args=(-s "${ADB_SERIAL}")

  adb start-server >/dev/null 2>&1 || true
  local devices
  devices="$(adb devices | awk 'NR>1 && $2=="device" {print $1}')"
  if [[ -z "${devices}" ]]; then
    die "No hay dispositivos ADB conectados. Conectá la UNO Q por USB-C y aceptá la depuración."
  fi
  echo "Dispositivos ADB: ${devices}"

  local archive
  archive="$(mktemp "${TMPDIR:-/tmp}/detector-tarjetas.XXXXXX.tar.gz")"
  tar -czf "${archive}" \
    --exclude='./.venv' --exclude='./venv' --exclude='./.git' \
    --exclude='./__pycache__' --exclude='./.pytest_cache' \
    --exclude='./PLAN.md' --exclude='./AUDIT_REPORT.md' \
    --exclude='./data/templates/*' \
    -C "${PROJECT_DIR}" .

  echo "Copiando proyecto a la UNO Q (${REMOTE_DIR})…"
  adb "${adb_args[@]}" push "${archive}" "/tmp/detector-tarjetas.tar.gz" || die "adb push falló"
  adb "${adb_args[@]}" shell "mkdir -p ${REMOTE_DIR_Q} && tar xzf /tmp/detector-tarjetas.tar.gz -C ${REMOTE_DIR_Q}" \
    || die "No se pudo descomprimir el proyecto en la UNO Q"

  echo "Verificando OpenCV en la UNO Q…"
  if ! adb "${adb_args[@]}" shell "python3 -c 'import cv2' >/dev/null 2>&1"; then
    echo "Instalando python3-opencv y v4l-utils (puede pedir contraseña)…"
    adb "${adb_args[@]}" shell "sudo apt-get update && sudo apt-get install -y python3-opencv v4l-utils" \
      || warn "La instalación por ADB falló; ejecutala manualmente en la terminal de App Lab."
  fi

  echo "Instalando servicio systemd…"
  local unit_remote_q service_remote_q
  unit_remote_q="$(quote_remote "${REMOTE_DIR}/systemd/${SERVICE_NAME}.service")"
  service_remote_q="$(quote_remote "/etc/systemd/system/${SERVICE_NAME}.service")"
  adb "${adb_args[@]}" shell "sudo cp ${unit_remote_q} ${service_remote_q} && sudo systemctl daemon-reload && sudo systemctl enable --now ${SERVICE_NAME_Q}" \
    || warn "No se pudo instalar el servicio automáticamente. Copiá la unidad a mano y ejecutá systemctl."

  echo
  echo "Despliegue ADB finalizado. Verificá con:"
  echo "  adb shell \"python3 -c 'import urllib.request;print(urllib.request.urlopen(\\\"http://127.0.0.1:${APP_PORT}/api/health\\\").read().decode())'\""
  echo "  o abrí http://<ip-de-la-unoq>:${APP_PORT}/ desde la notebook."
  rm -f "${archive}"
  exit 0
}

[[ "${VIA_ADB}" -eq 1 ]] && deploy_via_adb

# ---------------------------------------------------------------------------
# Validaciones previas
# ---------------------------------------------------------------------------
[[ -n "${SSH_HOST}" ]] || die "Falta --host <ip> (o UNOQ_HOST). Probá primero scripts/discover_unoq.sh"

if [[ -n "${PASSWORD}" ]] && ! command -v sshpass >/dev/null 2>&1; then
  die "Para usar --password se requiere 'sshpass'. Alternativa: configurá una clave SSH (ssh-copy-id ${SSH_USER}@${SSH_HOST})."
fi

SSH_OPTS=(-o ConnectTimeout=6 -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=15)

ssh_cmd() {
  if [[ -n "${PASSWORD}" ]]; then
    sshpass -p "${PASSWORD}" ssh "${SSH_OPTS[@]}" -p "${SSH_PORT}" "${SSH_USER}@${SSH_HOST}" "$1"
  else
    ssh "${SSH_OPTS[@]}" -p "${SSH_PORT}" "${SSH_USER}@${SSH_HOST}" "$1"
  fi
}

scp_cmd() {
  if [[ -n "${PASSWORD}" ]]; then
    sshpass -p "${PASSWORD}" scp "${SSH_OPTS[@]}" -P "${SSH_PORT}" "$1" "${SSH_USER}@${SSH_HOST}:$2"
  else
    scp "${SSH_OPTS[@]}" -P "${SSH_PORT}" "$1" "${SSH_USER}@${SSH_HOST}:$2"
  fi
}

try_ssh() {
  ssh_cmd "echo ok" >/dev/null 2>&1
}

# ---------------------------------------------------------------------------
# Conexión (con credenciales por defecto opcionales)
# ---------------------------------------------------------------------------
step "Probando SSH en ${SSH_USER}@${SSH_HOST}:${SSH_PORT}"
CONNECTED=0
if try_ssh; then
  CONNECTED=1
elif [[ "${TRY_DEFAULTS}" -eq 1 ]]; then
  if ! command -v sshpass >/dev/null 2>&1; then
    warn "No se puede probar contraseñas por defecto sin 'sshpass'. Se probarán solo claves SSH."
  else
    for candidate in arduino unoq root; do
      echo "  Probando contraseña por defecto '${candidate}' para ${SSH_USER}…"
      if UNOQ_PASSWORD="${candidate}" PASSWORD="${candidate}" sshpass -p "${candidate}" ssh "${SSH_OPTS[@]}" -p "${SSH_PORT}" "${SSH_USER}@${SSH_HOST}" "echo ok" >/dev/null 2>&1; then
        PASSWORD="${candidate}"
        CONNECTED=1
        echo "  ✔ Credenciales aceptadas (usuario ${SSH_USER})."
        break
      fi
    done
  fi
fi

if [[ "${CONNECTED}" -eq 0 ]]; then
  cat >&2 <<MSG

ERROR: no se pudo conectar por SSH a ${SSH_USER}@${SSH_HOST}:${SSH_PORT}.
Posibles causas y fallback:
  1) SSH no está habilitado en la UNO Q (el host responde ping pero no tiene el puerto 22).
     Conectá la placa por USB-C y usá Arduino App Lab para habilitar SSH, o
     ejecutá este mismo script con --via-adb.
  2) Usuario/clave incorrectos. Probá:
       ./scripts/deploy.sh --host ${SSH_HOST} --user arduino --try-defaults
       ./scripts/deploy.sh --host ${SSH_HOST} --user arduino --password '...'
  3) La UNO Q está en otra subred: revisá scripts/discover_unoq.sh --subnet ...
MSG
  exit 2
fi
echo "  ✔ SSH operativo."

# ---------------------------------------------------------------------------
# Copia del proyecto
# ---------------------------------------------------------------------------
step "Copiando proyecto a ${REMOTE_DIR}"
TAR_EXCLUDES=(
  --exclude='./.venv' --exclude='./venv' --exclude='./.git'
  --exclude='./__pycache__' --exclude='./.pytest_cache'
  --exclude='./PLAN.md' --exclude='./AUDIT_REPORT.md'
  --exclude='./data/templates/*'
)
if [[ -n "${PASSWORD}" ]]; then
  tar "${TAR_EXCLUDES[@]}" -czf - -C "${PROJECT_DIR}" . \
    | sshpass -p "${PASSWORD}" ssh "${SSH_OPTS[@]}" -p "${SSH_PORT}" "${SSH_USER}@${SSH_HOST}" \
        "mkdir -p ${REMOTE_DIR_Q} && tar -xzf - -C ${REMOTE_DIR_Q}"
else
  tar "${TAR_EXCLUDES[@]}" -czf - -C "${PROJECT_DIR}" . \
    | ssh "${SSH_OPTS[@]}" -p "${SSH_PORT}" "${SSH_USER}@${SSH_HOST}" \
        "mkdir -p ${REMOTE_DIR_Q} && tar -xzf - -C ${REMOTE_DIR_Q}"
fi
[[ $? -eq 0 ]] || die "Falló la copia del proyecto"
ssh_cmd "mkdir -p ${REMOTE_TEMPLATES_Q}" >/dev/null
echo "  ✔ Proyecto copiado."

# ---------------------------------------------------------------------------
# Dependencias del sistema
# ---------------------------------------------------------------------------
step "Verificando dependencias en la UNO Q"
if [[ "${SKIP_APT}" -eq 1 ]]; then
  echo "  (--skip-apt) Se omite la instalación de paquetes."
elif ssh_cmd "python3 -c 'import cv2' >/dev/null 2>&1"; then
  echo "  ✔ python3-opencv ya está instalado."
else
  echo "  Instalando python3-opencv y v4l-utils…"
  if ssh_cmd "sudo -n true >/dev/null 2>&1"; then
    ssh_cmd "sudo -n sh -c 'apt-get update -qq && apt-get install -y -qq python3-opencv v4l-utils'" \
      || warn "La instalación con apt falló. Ejecutá a mano: sudo apt-get install -y python3-opencv v4l-utils"
  else
    warn "sudo requiere contraseña en la UNO Q. Instalá manualmente:"
    echo "      ssh ${SSH_USER}@${SSH_HOST}"
    echo "      sudo apt-get update && sudo apt-get install -y python3-opencv v4l-utils"
    echo "    Luego volvé a ejecutar este script con --skip-apt."
    exit 3
  fi
fi

if ! ssh_cmd "python3 -c 'import cv2' >/dev/null 2>&1"; then
  die "OpenCV no está disponible en la UNO Q. Instalá python3-opencv y reintentá."
fi
echo "  ✔ OpenCV disponible."

# ---------------------------------------------------------------------------
# Unidad systemd
# ---------------------------------------------------------------------------
step "Instalando servicio ${SERVICE_NAME}"
UNIT_TMP="$(mktemp "${TMPDIR:-/tmp}/detector-unit.XXXXXX.service")"
trap 'rm -f "${UNIT_TMP}"' EXIT

sed -e "s|^User=.*|User=${SSH_USER}|" \
    -e "s|^WorkingDirectory=.*|WorkingDirectory=${REMOTE_DIR}|" \
    -e "s|^Environment=PORT=.*|Environment=PORT=${APP_PORT}|" \
    "${PROJECT_DIR}/systemd/${SERVICE_NAME}.service" >"${UNIT_TMP}"

if ! ssh_cmd "getent group video >/dev/null 2>&1"; then
  warn "El grupo 'video' no existe en la UNO Q; se quita SupplementaryGroups del servicio."
  sed -i '/^SupplementaryGroups=video/d' "${UNIT_TMP}"
fi

scp_cmd "${UNIT_TMP}" "/tmp/${SERVICE_NAME}.service" >/dev/null || die "No se pudo copiar la unidad systemd"
if ssh_cmd "sudo -n true >/dev/null 2>&1"; then
  ssh_cmd "sudo -n install -m 644 /tmp/${SERVICE_NAME_Q}.service /etc/systemd/system/${SERVICE_NAME_Q}.service" \
    || die "No se pudo instalar la unidad systemd"
  # Asegurar que el usuario pueda acceder a /dev/video*
  if ssh_cmd "getent group video >/dev/null 2>&1"; then
    ssh_cmd "sudo -n usermod -aG video ${SSH_USER_Q}" >/dev/null 2>&1 || true
  fi
  ssh_cmd "sudo -n systemctl daemon-reload && sudo -n systemctl enable ${SERVICE_NAME_Q}.service >/dev/null 2>&1 && sudo -n systemctl restart ${SERVICE_NAME_Q}.service" \
    || die "No se pudo habilitar/iniciar el servicio"
  echo "  ✔ Servicio instalado y arrancado."
else
  warn "sudo requiere contraseña: la unidad quedó en /tmp/${SERVICE_NAME}.service."
  echo "    Instalala manualmente:"
  echo "      ssh ${SSH_USER}@${SSH_HOST}"
  echo "      sudo install -m 644 /tmp/${SERVICE_NAME}.service /etc/systemd/system/"
  echo "      sudo systemctl daemon-reload && sudo systemctl enable --now ${SERVICE_NAME}"
  exit 3
fi

# ---------------------------------------------------------------------------
# Verificación
# ---------------------------------------------------------------------------
step "Verificando /api/health"
sleep 3
HEALTH="$(ssh_cmd "curl -fsS --max-time 5 'http://127.0.0.1:${APP_PORT}/api/health' 2>/dev/null || python3 -c 'import urllib.request; print(urllib.request.urlopen(\"http://127.0.0.1:${APP_PORT}/api/health\", timeout=5).read().decode())' 2>/dev/null" || true)"

if [[ -z "${HEALTH}" ]]; then
  warn "El servicio no respondió todavía. Últimos logs:"
  ssh_cmd "sudo -n journalctl -u ${SERVICE_NAME_Q} -n 25 --no-pager 2>/dev/null || journalctl -u ${SERVICE_NAME_Q} -n 25 --no-pager 2>/dev/null" || true
  warn "Revisá también el estado: systemctl status ${SERVICE_NAME}"
  exit 5
fi

echo "  ✔ ${HEALTH:0:240}"
echo
echo "Despliegue completado."
echo "  Web local en la UNO Q : http://127.0.0.1:${APP_PORT}/"
echo "  Web desde la notebook : http://${SSH_HOST}:${APP_PORT}/"
echo "  Stream MJPEG          : http://${SSH_HOST}:${APP_PORT}/stream.mjpg"
echo "  Estado del servicio   : ssh ${SSH_USER}@${SSH_HOST} 'systemctl status ${SERVICE_NAME}'"
