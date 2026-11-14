"""Constantes y configuración compartida de la aplicación.

Las rutas se resuelven a partir de la ubicación de este archivo para que la
aplicación funcione igual sin importar el directorio de trabajo desde el que se
lance (``scripts/run_local.sh``, systemd, tests, etc.).

Todas las constantes numéricas pueden sobrescribirse puntualmente por variable
de entorno cuando resulta útil (puerto, índice de cámara, rutas de datos).
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Rutas del proyecto
# ---------------------------------------------------------------------------
BASE_DIR: Path = Path(__file__).resolve().parent.parent
DATA_DIR: Path = BASE_DIR / "data"
BOARDS_JSON: Path = Path(os.environ.get("BOARDS_JSON", DATA_DIR / "boards.json"))
TEMPLATE_DIR: Path = Path(os.environ.get("TEMPLATE_DIR", DATA_DIR / "templates"))
WEB_DIR: Path = BASE_DIR / "web"
STATIC_DIR: Path = WEB_DIR / "static"

# ---------------------------------------------------------------------------
# Cámara
# ---------------------------------------------------------------------------
CAMERA_INDEX: int = int(os.environ.get("CAMERA_INDEX", "0"))  # /dev/video0
CAMERA_WIDTH: int = 640
CAMERA_HEIGHT: int = 480
CAMERA_FPS_TARGET: int = 15
CAMERA_RETRY_DELAY_S: float = 2.0
FRAME_QUEUE_SIZE: int = 2

# ---------------------------------------------------------------------------
# Detector ORB
# ---------------------------------------------------------------------------
DETECTOR_INTERVAL_MS: int = 250  # mínimo tiempo entre inferencias
ORB_FEATURES: int = int(os.environ.get("ORB_FEATURES", "500"))
ORB_EDGE_THRESHOLD: int = 31
ORB_PATCH_SIZE: int = 31
LOWE_RATIO: float = 0.75
MIN_MATCH_COUNT: int = 10
MIN_INLIERS: int = 15
RANSAC_REPROJ_THRESHOLD: float = 5.0
SCORE_THRESHOLD: float = 0.35
MIN_BOX_AREA_RATIO: float = 0.02
MAX_BOX_AREA_RATIO: float = 0.90
NMS_IOU_THRESHOLD: float = 0.30
INSTANCE_CENTER_DISTANCE_PX: float = 40.0
MAX_INSTANCES_PER_TEMPLATE: int = 4
# Si el polígono proyectado ocupa menos que esta fracción de su bounding box se
# considera una homografía degenerada y se descarta.
MIN_POLYGON_FILL_RATIO: float = 0.20

# ---------------------------------------------------------------------------
# Plantillas / modo enseñar
# ---------------------------------------------------------------------------
TEMPLATE_MIN_KEYPOINTS: int = 50
TEMPLATE_EXTENSION: str = ".npz"
DEFAULT_LABELS: tuple[str, ...] = (
    "arduino_uno_r3",
    "arduino_nano",
    "raspberry_pi_4",
    "raspberry_pi_5",
)

# ---------------------------------------------------------------------------
# Servidor HTTP
# ---------------------------------------------------------------------------
SERVER_HOST: str = os.environ.get("HOST", "0.0.0.0")
SERVER_PORT: int = int(os.environ.get("PORT", "8000"))
JPEG_QUALITY: int = 85
STREAM_BOUNDARY: bytes = b"--frame"
STREAM_CONTENT_TYPE: str = "multipart/x-mixed-replace; boundary=frame"
STREAM_MAX_FPS: int = 15
MAX_JSON_BODY_BYTES: int = 64 * 1024
CORS_ALLOW_ORIGIN: str = "*"
