#!/usr/bin/env bash
# Descubrimiento NO invasivo de la Arduino UNO Q en la red local.
#
# El script NO modifica la configuración de la red ni de ningún router:
#   - Muestra la interfaz, IP y vecinos ARP conocidos.
#   - Hace ping (1 paquete) a cada host del rango y prueba unos pocos puertos
#     TCP con timeout corto (22, 80, 443, 8000, 8080).
#   - Si encuentra SSH abierto, lee el banner; si encuentra el puerto 8000,
#     consulta /api/health de la aplicación.
#
# No asume ninguna IP concreta. Se puede acotar con --subnet o la variable
# UNOQ_CANDIDATE (solo para priorizar la salida en el informe).
#
# Uso:
#   ./scripts/discover_unoq.sh
#   ./scripts/discover_unoq.sh --subnet 192.168.1.0/24
#   ./scripts/discover_unoq.sh --no-ping            # solo tabla ARP
#   PING_TIMEOUT=2 MAX_PARALLEL=64 ./scripts/discover_unoq.sh
set -uo pipefail

PING_TIMEOUT="${PING_TIMEOUT:-1}"
PORT_TIMEOUT="${PORT_TIMEOUT:-1}"
MAX_PARALLEL="${MAX_PARALLEL:-32}"
PORTS="${PORTS:-22 80 443 8000 8080}"
SUBNET="${SUBNET:-}"
DO_PING=1
CANDIDATE="${UNOQ_CANDIDATE:-}"

usage() {
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --subnet)
      SUBNET="${2:-}"
      shift 2
      ;;
    --no-ping)
      DO_PING=0
      shift
      ;;
    --candidate)
      CANDIDATE="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Opción desconocida: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

command -v python3 >/dev/null 2>&1 || {
  echo "ERROR: se requiere python3 para calcular el rango de red." >&2
  exit 1
}

# ---------------------------------------------------------------------------
# Red local
# ---------------------------------------------------------------------------
IFACE=""
SRC_IP=""
CIDR=""
ADDRS=""
if command -v ip >/dev/null 2>&1; then
  # Se listan todas las IPv4 globales: la ruta por defecto puede apuntar a una
  # VPN (p. ej. CloudflareWARP) y no a la interfaz de la LAN real.
  ADDRS="$(ip -4 -o addr show scope global 2>/dev/null || true)"
  SELECTED="$(ADDRS="${ADDRS}" python3 - "${SUBNET}" <<'PY'
import ipaddress
import os
import sys

raw = os.environ.get("ADDRS", "")
entries = []
for line in raw.strip().splitlines():
    parts = line.split()
    if len(parts) >= 4:
        entries.append((parts[1], parts[3].split("/")[0], parts[3]))

def rank(entry):
    ip = ipaddress.ip_address(entry[1])
    if not ip.is_private:
        return 4
    text = str(ip)
    if text.startswith("192.168."):
        return 0
    if text.startswith("10."):
        return 1
    return 2

chosen = None
subnet = sys.argv[1] if len(sys.argv) > 1 else ""
if subnet:
    try:
        network = ipaddress.ip_network(subnet, strict=False)
        for entry in entries:
            if ipaddress.ip_address(entry[1]) in network:
                chosen = entry
                break
    except ValueError:
        pass
if chosen is None and entries:
    chosen = sorted(entries, key=rank)[0]
if chosen is not None:
    print(*chosen)
PY
)"
  read -r IFACE SRC_IP CIDR <<<"${SELECTED:-}"
fi

if [[ -z "${SUBNET}" ]]; then
  if [[ -n "${CIDR}" ]]; then
    SUBNET="${CIDR}"
  elif [[ -n "${SRC_IP}" ]]; then
    SUBNET="$(cut -d. -f1-3 <<<"${SRC_IP}").0/24"
  else
    echo "ERROR: no se pudo determinar la red local. Usá --subnet 192.168.x.0/24." >&2
    exit 1
  fi
fi

HOSTS_FILE="$(mktemp "${TMPDIR:-/tmp}/unoq-hosts.XXXXXX")"
RESULTS_FILE="$(mktemp "${TMPDIR:-/tmp}/unoq-scan.XXXXXX")"
trap 'rm -f "${HOSTS_FILE}" "${RESULTS_FILE}"' EXIT

python3 - "${SUBNET}" >"${HOSTS_FILE}" <<'PY'
import ipaddress
import sys

network = ipaddress.ip_network(sys.argv[1], strict=False)
for host in network.hosts():
    print(host)
PY

# ---------------------------------------------------------------------------
# Escaneo de hosts (ping + puertos TCP)
# ---------------------------------------------------------------------------
scan_host() {
  local ip="$1" p open=""
  if ping -c 1 -W "${PING_TIMEOUT}" -n "${ip}" >/dev/null 2>&1; then
    for p in ${PORTS}; do
      if timeout "${PORT_TIMEOUT}" bash -c "exec 3<>/dev/tcp/${ip}/${p}" 2>/dev/null; then
        open+="${p} "
      fi
    done
    printf 'UP %s OPEN %s\n' "${ip}" "${open:-none}"
  fi
}
export -f scan_host
export PING_TIMEOUT PORT_TIMEOUT PORTS

echo "=============================================================="
echo " Descubrimiento de Arduino UNO Q (no invasivo)"
echo "=============================================================="
if [[ -n "${ADDRS}" ]]; then
  echo "IPv4 locales   :"
  awk '{printf "  - %-14s %s\n", $2, $4}' <<<"${ADDRS}"
fi
echo "Interfaz       : ${IFACE:-desconocida}"
echo "Notebook       : ${SRC_IP:-desconocida}"
echo "Rango          : ${SUBNET}"
echo "Puertos        : ${PORTS}"
echo "Paralelismo    : ${MAX_PARALLEL}"
echo "--------------------------------------------------------------"

echo
echo "[1] Vecinos ARP/IPv6 conocidos"
if command -v ip >/dev/null 2>&1; then
  ip neigh show 2>/dev/null || true
else
  echo "  (comando 'ip' no disponible)"
fi

if [[ "${DO_PING}" -eq 1 ]]; then
  echo
  echo "[2] Ping + puertos TCP (puede tardar ~$(( $(wc -l <"${HOSTS_FILE}") / MAX_PARALLEL + 2 ))s)"
  if command -v ping >/dev/null 2>&1; then
    if [[ -s "${HOSTS_FILE}" ]]; then
      # shellcheck disable=SC2016
      xargs -P "${MAX_PARALLEL}" -n 1 bash -c 'scan_host "$1"' _ <"${HOSTS_FILE}" >"${RESULTS_FILE}" 2>/dev/null || true
    fi
  else
    echo "  AVISO: 'ping' no está instalado; se omite el barrido (usá --no-ping)."
  fi
else
  echo
  echo "[2] Barrido de ping omitido (--no-ping)"
fi

if [[ -s "${RESULTS_FILE}" ]]; then
  sort -t. -k4,4n "${RESULTS_FILE}" >"${RESULTS_FILE}.sorted"
  mv "${RESULTS_FILE}.sorted" "${RESULTS_FILE}"
fi

echo
echo "[3] Hosts que respondieron"
if [[ -s "${RESULTS_FILE}" ]]; then
  cat "${RESULTS_FILE}"
else
  echo "  (ninguno)"
fi

get_ssh_banner() {
  local ip="$1"
  timeout 3 bash -c "exec 3<>/dev/tcp/${ip}/22; head -c 120 <&3" 2>/dev/null | tr -d '\r\n' || true
}

check_health() {
  local ip="$1"
  if command -v curl >/dev/null 2>&1; then
    curl -fsS --max-time 2 "http://${ip}:8000/api/health" 2>/dev/null || true
  else
    python3 - "${ip}" <<'PY' 2>/dev/null || true
import json
import sys
import urllib.request

url = f"http://{sys.argv[1]}:8000/api/health"
with urllib.request.urlopen(url, timeout=2) as response:
    print(response.read().decode("utf-8"))
PY
  fi
}

echo
echo "[4] Clasificación de candidatos"
FOUND_SSH=0
FOUND_APP=0
FOUND_LOCKED=0

if [[ -s "${RESULTS_FILE}" ]]; then
  while read -r _ ip _ open_ports; do
    [[ -z "${ip:-}" ]] && continue
    # La propia notebook no es un candidato
    [[ "${ip}" == "${SRC_IP}" ]] && continue
    note=""
    if [[ " ${open_ports} " == *" 8000 "* ]]; then
      health="$(check_health "${ip}")"
      if [[ -n "${health}" ]]; then
        note="app detector-tarjetas YA DESPLEGADA (${health:0:96}…)"
        FOUND_APP=1
      fi
    fi
    if [[ " ${open_ports} " == *" 22 "* ]]; then
      banner="$(get_ssh_banner "${ip}")"
      note="SSH abierto${banner:+ (banner: ${banner:0:48})} -> probá scripts/deploy.sh --host ${ip}"
      FOUND_SSH=1
    elif [[ "${open_ports}" == "none" ]]; then
      note="sin puertos TCP abiertos: candidato BLOQUEADO (SSH no habilitado; ver fallback adb/App Lab)"
      FOUND_LOCKED=1
    fi
    [[ -n "${CANDIDATE}" && "${ip}" == "${CANDIDATE}" ]] && note="[candidato conocido] ${note}"
    printf '  %-15s %s\n' "${ip}" "${note:-respondió ping}"
  done <"${RESULTS_FILE}"
else
  echo "  (sin datos: revisá que la notebook esté en la misma red que la UNO Q)"
fi

echo
echo "[5] Resumen"
if [[ "${FOUND_APP}" -eq 1 ]]; then
  echo "  ✔ La aplicación ya responde en http://<ip>:8000/"
fi
if [[ "${FOUND_SSH}" -eq 1 ]]; then
  echo "  ✔ Hay al menos un host con SSH abierto:"
  echo "      ./scripts/deploy.sh --host <ip> --user arduino"
fi
if [[ "${FOUND_LOCKED}" -eq 1 ]]; then
  echo "  ⚠ Hay hosts vivos sin puertos abiertos (posible UNO Q con SSH deshabilitado)."
  echo "    Fallback: conectá la placa por USB-C y usá 'adb devices' o Arduino App Lab"
  echo "    para habilitar SSH o copiar el proyecto (ver README.md, sección Despliegue)."
fi
if [[ "${FOUND_APP}" -eq 0 && "${FOUND_SSH}" -eq 0 && "${FOUND_LOCKED}" -eq 0 ]]; then
  echo "  ✘ No se encontraron candidatos. Verificá la red WiFi/cable y el rango escaneado."
fi

echo
echo "Sugerencia: si la UNO Q está en otra subred, repetí con"
echo "  ./scripts/discover_unoq.sh --subnet 192.168.2.0/24"
echo "No se modificó ninguna configuración de red."
