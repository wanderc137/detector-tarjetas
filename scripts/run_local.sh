#!/usr/bin/env bash
# Arranque local en el notebook (x86_64).
#
# Crea/actualiza un entorno virtual dentro del proyecto, instala las
# dependencias de desarrollo y arranca el servidor. No requiere la UNO Q.
#
# Uso:
#   ./scripts/run_local.sh            # puerto 8000, cámara /dev/video0
#   PORT=8010 ./scripts/run_local.sh  # otro puerto
#   CAMERA_INDEX=2 ./scripts/run_local.sh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-$PROJECT_DIR/.venv}"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  echo "[run_local] Creando entorno virtual en $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

if [[ "${SKIP_INSTALL:-0}" != "1" ]]; then
  echo "[run_local] Instalando dependencias de requirements-dev.txt"
  python -m pip install --upgrade pip >/dev/null
  python -m pip install -q -r requirements-dev.txt
fi

export PORT="${PORT:-8000}"
export CAMERA_INDEX="${CAMERA_INDEX:-0}"
export HOST="${HOST:-0.0.0.0}"

echo "[run_local] http://localhost:${PORT}/  (cámara /dev/video${CAMERA_INDEX})"
exec python -m app.server
