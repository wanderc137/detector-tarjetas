#!/usr/bin/env bash
# Despliegue del detector de tarjetas en la Arduino UNO Q (lado Linux Debian ARM64).
#
# Copia el proyecto por SSH (tar sobre ssh, sin rsync) o por USB-C/adb,
# verifica python3-opencv, renderiza la unidad systemd desde la plantilla
# (systemd/detector-tarjetas.service) y verifica el servicio.
#
# La unidad es una PLANTILLA: los placeholders @USER@, @GROUP@, @WORKDIR@ y
# @PORT@ se resuelven en cada despliegue. La configuracion local de la placa
# (CAMERA_URL, CAMERA_INDEX, ...) vive en /etc/default/detector-tarjetas y
# NUNCA se sobrescribe si ya existe.
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
FORCE_UNIT=0
ADB_SERIAL="${ADB_SERIAL:-}"

# Valores objetivo del servicio (se resuelven más abajo).
USER_EXPLICIT=0
GROUP_EXPLICIT=0
WORKDIR_EXPLICIT=0
TARGET_USER=""
TARGET_GROUP=""
TARGET_WORKDIR=""

# Estado detectado en la placa.
LOAD_STATE=""
DETECTED_USER=""
DETECTED_GROUP=""
DETECTED_WORKDIR=""
DETECTED_CAMERA_URL=""
DETECTED_CAMERA_INDEX=""

MODE=""
ADB_ARGS=()
ENV_FILE_REMOTE="/etc/default/${SERVICE_NAME}"

usage() {
  cat <<'EOF'
Uso:
  ./scripts/deploy.sh --host <ip> [--user <usuario>]
  UNOQ_HOST=<ip> UNOQ_PASSWORD=<pass> ./scripts/deploy.sh
  ./scripts/deploy.sh --via-adb [--adb-serial <serial>]

Opciones:
  --host <ip>          IP/host de la UNO Q (modo SSH)
  --user <usuario>     usuario SSH y del servicio
                       (default: el de la unidad existente o arduino)
  --group <grupo>      grupo del servicio
                       (default: el de la unidad existente o el usuario)
  --port <puerto>      puerto SSH (default: 22)
  --password <pass>    contraseña SSH (requiere sshpass)
  --remote-dir <ruta>  directorio de instalación
                       (default: WorkingDirectory actual o /home/<usuario>/detector-tarjetas)
  --app-port <puerto>  puerto HTTP del detector (default: 8000)
  --skip-apt           no instalar paquetes del sistema
  --try-defaults       probar credenciales SSH por defecto
  --via-adb            desplegar por USB-C usando adb
  --adb-serial <s>     serial del dispositivo adb
  --force-unit         permitir reemplazar una unidad existente con
                       User/Group/WorkingDirectory distintos
  -h, --help           esta ayuda

La unidad systemd se renderiza desde systemd/detector-tarjetas.service.
La configuracion local (/etc/default/detector-tarjetas) solo se siembra si
no existe; un redeploy nunca pisa CAMERA_URL/CAMERA_INDEX de la placa.
EOF
}

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
# Lógica pura: render de la unidad, parseo del estado y semilla de entorno.
# Son funciones sin efectos remotos para poder probarlas por separado.
# ---------------------------------------------------------------------------

render_unit() { # render_unit <template> <user> <group> <workdir> <port>
  local template="$1" user="$2" group="$3" workdir="$4" port="$5"
  sed -e "s|@USER@|${user}|g" \
      -e "s|@GROUP@|${group}|g" \
      -e "s|@WORKDIR@|${workdir}|g" \
      -e "s|@PORT@|${port}|g" \
      "${template}"
}

# Lee por stdin la salida de:
#   systemctl show <servicio> -p LoadState -p User -p Group -p WorkingDirectory
#   echo '###UNIT###'
#   cat /etc/systemd/system/<servicio>.service
# y deja el resultado en las variables DETECTED_* / LOAD_STATE.
parse_unit_state() {
  local line in_unit=0
  LOAD_STATE=""
  DETECTED_USER=""
  DETECTED_GROUP=""
  DETECTED_WORKDIR=""
  DETECTED_CAMERA_URL=""
  DETECTED_CAMERA_INDEX=""
  while IFS= read -r line; do
    line="${line%$'\r'}"
    if [[ "${line}" == "###UNIT###" ]]; then
      in_unit=1
      continue
    fi
    if (( in_unit )); then
      case "${line}" in
        Environment=CAMERA_URL=*) DETECTED_CAMERA_URL="${line#Environment=CAMERA_URL=}" ;;
        Environment=CAMERA_INDEX=*) DETECTED_CAMERA_INDEX="${line#Environment=CAMERA_INDEX=}" ;;
      esac
    else
      case "${line}" in
        LoadState=*) LOAD_STATE="${line#LoadState=}" ;;
        User=*) DETECTED_USER="${line#User=}" ;;
        Group=*) DETECTED_GROUP="${line#Group=}" ;;
        WorkingDirectory=*) DETECTED_WORKDIR="${line#WorkingDirectory=}" ;;
      esac
    fi
  done
}

# Genera el contenido de /etc/default/<servicio>. Se usa solo cuando el
# archivo no existe todavía en la placa.
compose_env_file() { # compose_env_file <camera_url> <camera_index>
  local camera_url="${1:-}" camera_index="${2:-}"
  cat <<EOF
# Generado por scripts/deploy.sh a partir de la unidad existente.
# Editar a mano en la placa; los redeploys NO sobrescriben este archivo.
# CAMERA_URL tiene prioridad sobre CAMERA_INDEX cuando esta definida.
CAMERA_URL=${camera_url}
CAMERA_INDEX=${camera_index}
PORT=${APP_PORT}
LOG_LEVEL=INFO
EOF
}

# ---------------------------------------------------------------------------
# Transporte remoto (SSH o adb). Todo comando remoto pasa por acá.
# ---------------------------------------------------------------------------
remote_exec() { # remote_exec <comando>
  if [[ "${MODE}" == "adb" ]]; then
    adb "${ADB_ARGS[@]}" shell "$1"
  else
    ssh_cmd "$1"
  fi
}

remote_exec_q() { # remote_exec_q <comando>: silencioso, solo código de salida
  remote_exec "$1" >/dev/null 2>&1
}

remote_put() { # remote_put <archivo_local> <ruta_remota>
  if [[ "${MODE}" == "adb" ]]; then
    adb "${ADB_ARGS[@]}" push "$1" "$2" >/dev/null
  else
    scp_cmd "$1" "$2" >/dev/null
  fi
}

sudo_ok() { remote_exec_q "sudo -n true"; }

sudo_install() { # sudo_install <origen_remoto> <destino_remoto> [modo]
  local src="$1" dst="$2" mode="${3:-644}"
  remote_exec "sudo -n install -m ${mode} $(quote_remote "${src}") $(quote_remote "${dst}")"
}

# ---------------------------------------------------------------------------
# Argumentos
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) SSH_HOST="${2:-}"; shift 2 ;;
    --user) SSH_USER="${2:-}"; USER_EXPLICIT=1; shift 2 ;;
    --group) TARGET_GROUP="${2:-}"; GROUP_EXPLICIT=1; shift 2 ;;
    --port) SSH_PORT="${2:-}"; shift 2 ;;
    --password) PASSWORD="${2:-}"; shift 2 ;;
    --remote-dir) REMOTE_DIR="${2:-}"; WORKDIR_EXPLICIT=1; shift 2 ;;
    --app-port) APP_PORT="${2:-}"; shift 2 ;;
    --skip-apt) SKIP_APT=1; shift ;;
    --try-defaults) TRY_DEFAULTS=1; shift ;;
    --via-adb) VIA_ADB=1; shift ;;
    --adb-serial) ADB_SERIAL="${2:-}"; shift 2 ;;
    --force-unit) FORCE_UNIT=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Opción desconocida: $1" >&2; usage >&2; exit 2 ;;
  esac
done

# ---------------------------------------------------------------------------
# Validación estricta de TODO lo que se interpola en comandos remotos
# ---------------------------------------------------------------------------
validate_identifier "SERVICE_NAME" "${SERVICE_NAME}"
validate_identifier "SSH_USER" "${SSH_USER}"
validate_port "SSH_PORT" "${SSH_PORT}"
validate_port "APP_PORT" "${APP_PORT}"
if [[ "${GROUP_EXPLICIT}" -eq 1 ]]; then
  validate_identifier "GROUP" "${TARGET_GROUP}"
fi
if [[ -n "${SSH_HOST}" ]]; then
  validate_host "${SSH_HOST}"
fi
if [[ -n "${ADB_SERIAL}" ]]; then
  [[ "${ADB_SERIAL}" =~ ^[A-Za-z0-9_.:-]+$ ]] \
    || die "ADB_SERIAL inválido: '${ADB_SERIAL}'."
fi
if [[ "${WORKDIR_EXPLICIT}" -eq 1 ]]; then
  validate_remote_dir "${REMOTE_DIR}"
fi

SERVICE_NAME_Q="$(quote_remote "${SERVICE_NAME}")"
APP_PORT_Q="$(quote_remote "${APP_PORT}")"

# ---------------------------------------------------------------------------
# SSH
# ---------------------------------------------------------------------------
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

connect_ssh() {
  [[ -n "${SSH_HOST}" ]] || die "Falta --host <ip> (o UNOQ_HOST). Probá primero scripts/discover_unoq.sh"

  if [[ -n "${PASSWORD}" ]] && ! command -v sshpass >/dev/null 2>&1; then
    die "Para usar --password se requiere 'sshpass'. Alternativa: configurá una clave SSH (ssh-copy-id ${SSH_USER}@${SSH_HOST})."
  fi

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
        if sshpass -p "${candidate}" ssh "${SSH_OPTS[@]}" -p "${SSH_PORT}" "${SSH_USER}@${SSH_HOST}" "echo ok" >/dev/null 2>&1; then
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
}

# ---------------------------------------------------------------------------
# ADB
# ---------------------------------------------------------------------------
connect_adb() {
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

  adb start-server >/dev/null 2>&1 || true
  local devices
  devices="$(adb devices | awk 'NR>1 && $2=="device" {print $1}')"
  if [[ -z "${devices}" ]]; then
    die "No hay dispositivos ADB conectados. Conectá la UNO Q por USB-C y aceptá la depuración."
  fi
  if [[ -z "${ADB_SERIAL}" ]]; then
    ADB_SERIAL="$(printf '%s\n' "${devices}" | head -n1)"
  fi
  echo "Dispositivo ADB: ${ADB_SERIAL}"
  ADB_ARGS=(-s "${ADB_SERIAL}")
}

# ---------------------------------------------------------------------------
# Detección del estado instalado y resolución de los valores objetivo
# ---------------------------------------------------------------------------
detect_remote_state() {
  step "Detectando unidad ${SERVICE_NAME} instalada"
  local raw
  raw="$(remote_exec "systemctl show ${SERVICE_NAME_Q} -p LoadState -p User -p Group -p WorkingDirectory 2>/dev/null; echo '###UNIT###'; cat /etc/systemd/system/${SERVICE_NAME}.service 2>/dev/null || true" || true)"
  # Here-string (no pipe): parse_unit_state debe correr en el shell actual para
  # que las variables DETECTED_* sobrevivan.
  parse_unit_state <<<"${raw}"

  if [[ "${LOAD_STATE}" == "loaded" ]]; then
    echo "  Unidad existente: User=${DETECTED_USER:-<vacío>} Group=${DETECTED_GROUP:-<vacío>} WorkingDirectory=${DETECTED_WORKDIR:-<vacío>}"
    [[ -n "${DETECTED_CAMERA_URL}" ]] && echo "  CAMERA_URL existente: ${DETECTED_CAMERA_URL}"
  else
    echo "  No hay unidad instalada (LoadState=${LOAD_STATE:-desconocido})."
  fi
}

resolve_targets() {
  if [[ "${USER_EXPLICIT}" -eq 0 && -n "${DETECTED_USER}" ]]; then
    SSH_USER="${DETECTED_USER}"
  fi
  TARGET_USER="${SSH_USER}"
  validate_identifier "USER" "${TARGET_USER}"

  if [[ "${GROUP_EXPLICIT}" -eq 1 ]]; then
    TARGET_GROUP="${TARGET_GROUP}"
  elif [[ -n "${DETECTED_GROUP}" ]]; then
    TARGET_GROUP="${DETECTED_GROUP}"
  else
    TARGET_GROUP="${TARGET_USER}"
  fi
  validate_identifier "GROUP" "${TARGET_GROUP}"

  if [[ "${WORKDIR_EXPLICIT}" -eq 1 ]]; then
    TARGET_WORKDIR="${REMOTE_DIR}"
  elif [[ -n "${DETECTED_WORKDIR}" ]]; then
    TARGET_WORKDIR="${DETECTED_WORKDIR}"
  else
    TARGET_WORKDIR="/home/${TARGET_USER}/detector-tarjetas"
  fi
  validate_remote_dir "${TARGET_WORKDIR}"

  if [[ "${LOAD_STATE}" == "loaded" && "${FORCE_UNIT}" -eq 0 ]]; then
    local diffs=()
    [[ -n "${DETECTED_USER}" && "${DETECTED_USER}" != "${TARGET_USER}" ]] \
      && diffs+=("User: ${DETECTED_USER} -> ${TARGET_USER}")
    [[ -n "${DETECTED_GROUP}" && "${DETECTED_GROUP}" != "${TARGET_GROUP}" ]] \
      && diffs+=("Group: ${DETECTED_GROUP} -> ${TARGET_GROUP}")
    [[ -n "${DETECTED_WORKDIR}" && "${DETECTED_WORKDIR}" != "${TARGET_WORKDIR}" ]] \
      && diffs+=("WorkingDirectory: ${DETECTED_WORKDIR} -> ${TARGET_WORKDIR}")
    if (( ${#diffs[@]} > 0 )); then
      cat >&2 <<MSG

ERROR: la unidad ${SERVICE_NAME} instalada difiere del objetivo:
$(printf '  - %s\n' "${diffs[@]}")
Un redeploy normal no reemplaza una unidad con otro User/Group/WorkingDirectory.
Pasá los mismos valores o repetí con --force-unit si querés reemplazarla.
MSG
      exit 6
    fi
  elif [[ "${LOAD_STATE}" == "loaded" ]]; then
    warn "--force-unit: se reemplazará la unidad existente aunque cambie User/Group/WorkingDirectory."
  fi

  echo "  Objetivo: User=${TARGET_USER} Group=${TARGET_GROUP} WorkingDirectory=${TARGET_WORKDIR} PORT=${APP_PORT}"
}

# ---------------------------------------------------------------------------
# Copia del proyecto
# ---------------------------------------------------------------------------
TAR_EXCLUDES=(
  --exclude='./.venv' --exclude='./venv' --exclude='./.git'
  --exclude='./__pycache__' --exclude='./.pytest_cache'
  --exclude='./PLAN.md' --exclude='./AUDIT_REPORT.md'
  --exclude='./data/templates/*'
)

copy_project() {
  step "Copiando proyecto a ${TARGET_WORKDIR}"
  local archive remote_archive="/tmp/${SERVICE_NAME}.tar.gz"
  archive="$(mktemp "${TMPDIR:-/tmp}/${SERVICE_NAME}.XXXXXX.tar.gz")"
  tar -czf "${archive}" "${TAR_EXCLUDES[@]}" -C "${PROJECT_DIR}" .
  remote_put "${archive}" "${remote_archive}"
  rm -f "${archive}"
  remote_exec "mkdir -p $(quote_remote "${TARGET_WORKDIR}") && tar xzf $(quote_remote "${remote_archive}") -C $(quote_remote "${TARGET_WORKDIR}")" \
    || die "No se pudo descomprimir el proyecto en la UNO Q (¿permisos de ${TARGET_WORKDIR}?)"
  remote_exec "mkdir -p $(quote_remote "${TARGET_WORKDIR}/data/templates")" >/dev/null
  echo "  ✔ Proyecto copiado."
}

# ---------------------------------------------------------------------------
# Dependencias del sistema
# ---------------------------------------------------------------------------
check_dependencies() {
  step "Verificando dependencias en la UNO Q"
  if remote_exec_q "python3 -c 'import cv2'"; then
    echo "  ✔ python3-opencv ya está instalado."
    return 0
  fi
  if [[ "${SKIP_APT}" -eq 1 ]]; then
    die "OpenCV no está disponible y se pasó --skip-apt. Instalá python3-opencv a mano."
  fi
  if ! sudo_ok; then
    warn "OpenCV no está instalado y sudo requiere contraseña. Instalalo a mano:"
    echo "      sudo apt-get update && sudo apt-get install -y python3-opencv v4l-utils"
    echo "    Luego volvé a ejecutar este script con --skip-apt."
    exit 3
  fi
  echo "  Instalando python3-opencv y v4l-utils…"
  remote_exec "sudo -n sh -c 'apt-get update -qq && apt-get install -y -qq python3-opencv v4l-utils'" \
    || die "La instalación con apt falló. Ejecutala a mano: sudo apt-get install -y python3-opencv v4l-utils"
  remote_exec_q "python3 -c 'import cv2'" \
    || die "OpenCV sigue sin estar disponible tras la instalación."
  echo "  ✔ OpenCV disponible."
}

# ---------------------------------------------------------------------------
# Configuración local: sembrar /etc/default/<servicio> solo si no existe
# ---------------------------------------------------------------------------
seed_env_file() {
  if remote_exec_q "test -e $(quote_remote "${ENV_FILE_REMOTE}")"; then
    echo "  ✔ ${ENV_FILE_REMOTE} ya existe; se preserva (no se sobrescribe)."
    return 0
  fi
  local seed_tmp remote_seed="/tmp/${SERVICE_NAME}.env"
  seed_tmp="$(mktemp "${TMPDIR:-/tmp}/${SERVICE_NAME}.env.XXXXXX")"
  compose_env_file "${DETECTED_CAMERA_URL}" "${DETECTED_CAMERA_INDEX}" >"${seed_tmp}"
  if remote_put "${seed_tmp}" "${remote_seed}"; then
    sudo_install "${remote_seed}" "${ENV_FILE_REMOTE}" 644 \
      && echo "  ✔ ${ENV_FILE_REMOTE} sembrado (CAMERA_URL='${DETECTED_CAMERA_URL}', CAMERA_INDEX='${DETECTED_CAMERA_INDEX}')."
  else
    warn "No se pudo copiar ${remote_seed}; se omite la semilla de ${ENV_FILE_REMOTE}."
  fi
  rm -f "${seed_tmp}"
}

# ---------------------------------------------------------------------------
# Unidad systemd (renderizada desde la plantilla)
# ---------------------------------------------------------------------------
install_unit() {
  step "Instalando servicio ${SERVICE_NAME}"

  local unit_tmp remote_unit="/tmp/${SERVICE_NAME}.service"
  unit_tmp="$(mktemp "${TMPDIR:-/tmp}/${SERVICE_NAME}.unit.XXXXXX")"
  render_unit "${PROJECT_DIR}/systemd/${SERVICE_NAME}.service" \
    "${TARGET_USER}" "${TARGET_GROUP}" "${TARGET_WORKDIR}" "${APP_PORT}" >"${unit_tmp}"

  if ! remote_exec_q "getent group video"; then
    warn "El grupo 'video' no existe en la UNO Q; se quita SupplementaryGroups del servicio."
    sed -i '/^SupplementaryGroups=video/d' "${unit_tmp}"
  fi

  if ! remote_put "${unit_tmp}" "${remote_unit}"; then
    rm -f "${unit_tmp}"
    die "No se pudo copiar la unidad systemd a ${remote_unit}"
  fi
  rm -f "${unit_tmp}"

  if ! sudo_ok; then
    warn "sudo requiere contraseña: NO se instala nada a medias."
    echo "    La unidad renderizada quedó en ${remote_unit} de la UNO Q."
    echo "    Aplicá los cambios manualmente:"
    echo "      sudo install -m 644 ${remote_unit} /etc/systemd/system/${SERVICE_NAME}.service"
    echo "      sudo systemctl daemon-reload"
    echo "      sudo systemctl enable ${SERVICE_NAME}"
    echo "      sudo systemctl restart ${SERVICE_NAME}"
    echo "    Verificá también ${ENV_FILE_REMOTE} (ver systemd/${SERVICE_NAME}.env.example)."
    exit 3
  fi

  seed_env_file

  sudo_install "${remote_unit}" "/etc/systemd/system/${SERVICE_NAME}.service" 644 \
    || die "No se pudo instalar la unidad systemd"
  if remote_exec_q "getent group video"; then
    remote_exec "sudo -n usermod -aG video $(quote_remote "${TARGET_USER}")" >/dev/null 2>&1 || true
  fi
  remote_exec "sudo -n systemctl daemon-reload && sudo -n systemctl enable $(quote_remote "${SERVICE_NAME}") >/dev/null 2>&1 && sudo -n systemctl restart $(quote_remote "${SERVICE_NAME}")" \
    || die "No se pudo habilitar/iniciar el servicio"
  echo "  ✔ Servicio instalado y arrancado."
}

# ---------------------------------------------------------------------------
# Verificación
# ---------------------------------------------------------------------------
verify_service() {
  step "Verificando servicio y /api/health"
  local enabled active health
  enabled="$(remote_exec "systemctl is-enabled $(quote_remote "${SERVICE_NAME}") 2>/dev/null" | tr -d '\r' | head -n1)"
  active="$(remote_exec "systemctl is-active $(quote_remote "${SERVICE_NAME}") 2>/dev/null" | tr -d '\r' | head -n1)"
  echo "  is-enabled=${enabled:-desconocido} is-active=${active:-desconocido}"

  if [[ "${enabled}" != "enabled" ]]; then
    remote_exec "systemctl status $(quote_remote "${SERVICE_NAME}") --no-pager -n 15 2>&1 || true" || true
    die "El servicio no quedó habilitado (is-enabled=${enabled:-desconocido})."
  fi
  if [[ "${active}" != "active" ]]; then
    remote_exec "journalctl -u $(quote_remote "${SERVICE_NAME}") -n 25 --no-pager 2>&1 || true" || true
    die "El servicio no quedó activo (is-active=${active:-desconocido})."
  fi

  sleep 3
  health="$(remote_exec "curl -fsS --max-time 5 'http://127.0.0.1:${APP_PORT}/api/health' 2>/dev/null || python3 -c 'import urllib.request; print(urllib.request.urlopen(\"http://127.0.0.1:${APP_PORT}/api/health\", timeout=5).read().decode())' 2>/dev/null" | tr -d '\r' || true)"

  if [[ -z "${health}" ]]; then
    remote_exec "journalctl -u $(quote_remote "${SERVICE_NAME}") -n 25 --no-pager 2>&1 || true" || true
    die "El servicio está activo pero /api/health no respondió en el puerto ${APP_PORT}."
  fi
  echo "  ✔ ${health:0:240}"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
  if [[ "${VIA_ADB}" -eq 1 ]]; then
    MODE="adb"
    connect_adb
  else
    MODE="ssh"
    connect_ssh
  fi

  detect_remote_state
  resolve_targets

  copy_project
  check_dependencies
  install_unit
  verify_service

  echo
  echo "Despliegue completado."
  if [[ "${MODE}" == "ssh" ]]; then
    echo "  Web local en la UNO Q : http://127.0.0.1:${APP_PORT}/"
    echo "  Web desde la notebook : http://${SSH_HOST}:${APP_PORT}/"
    echo "  Stream MJPEG          : http://${SSH_HOST}:${APP_PORT}/stream.mjpg"
    echo "  Estado del servicio   : ssh ${TARGET_USER}@${SSH_HOST} 'systemctl status ${SERVICE_NAME}'"
  else
    echo "  Web local en la UNO Q : http://127.0.0.1:${APP_PORT}/"
    echo "  Estado del servicio   : adb shell 'systemctl status ${SERVICE_NAME}'"
  fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
