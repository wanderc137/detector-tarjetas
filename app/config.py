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
# URL de un stream remoto (p.ej. cámara IP / IP Webcam). Si está definida,
# tiene prioridad sobre CAMERA_INDEX.
CAMERA_URL: str = os.environ.get("CAMERA_URL", "")
# Archivo que persiste la URL de cámara elegida desde la web. Tiene prioridad
# sobre CAMERA_URL y CAMERA_INDEX para que sobreviva a reinicios del servicio.
CAMERA_URL_FILE: Path = Path(os.environ.get("CAMERA_URL_FILE", DATA_DIR / "camera_url.txt"))
# Resolución solicitada al abrir la cámara: es un valor de fallback/negociación,
# no una garantía. La calibración del área de escaneo (``DEFAULT_SCAN_QUAD``)
# asume un frame **portrait 480×640** (ancho 480, alto 640). Si la fuente
# entrega otra resolución (por ejemplo 640×480 landscape), el cuadrado
# calibrado se deforma y deja de coincidir con las marcas en T: en ese caso hay
# que ajustar ``SCAN_QUAD`` o rotar/reconfigurar la cámara. No cambiar estos
# valores por defecto sin revalidar el quad.
CAMERA_WIDTH: int = 640
CAMERA_HEIGHT: int = 480
CAMERA_FPS_TARGET: int = 15
CAMERA_RETRY_DELAY_S: float = 2.0
FRAME_QUEUE_SIZE: int = 2


def load_camera_source() -> "int | str":
    """Devuelve la fuente de cámara efectiva.

    Precedencia: (1) override persistido desde la web (``CAMERA_URL_FILE``),
    (2) variable ``CAMERA_URL``, (3) ``CAMERA_INDEX``.
    """
    if CAMERA_URL_FILE.exists():
        text = CAMERA_URL_FILE.read_text(encoding="utf-8").strip()
        if text:
            return text
    if CAMERA_URL:
        return CAMERA_URL
    return CAMERA_INDEX

# ---------------------------------------------------------------------------
# Detector ORB
# ---------------------------------------------------------------------------
DETECTOR_INTERVAL_MS: int = 250  # mínimo tiempo entre inferencias
ORB_FEATURES: int = int(os.environ.get("ORB_FEATURES", "500"))
ORB_EDGE_THRESHOLD: int = 31
ORB_PATCH_SIZE: int = 31
LOWE_RATIO: float = 0.75
MIN_MATCH_COUNT: int = 10
MIN_INLIERS: int = 20
RANSAC_REPROJ_THRESHOLD: float = 5.0
SCORE_THRESHOLD: float = 0.60
MIN_BOX_AREA_RATIO: float = 0.02
MAX_BOX_AREA_RATIO: float = 0.90
# Fracción mínima del quad proyectado que debe caer dentro del área de escaneo
# (ROI). Un quad mayormente fuera del ROI es señal de un falso positivo de fondo
# (las plantillas capturadas a frame completo arrastran la hoja/brazo estático).
MIN_VISIBLE_QUAD_RATIO: float = 0.80
# Área de escaneo donde el usuario debe apoyar la tarjeta, como cuadrilátero
# normalizado en orden TL, TR, BR, BL. Es un CUADRADO en píxeles (mismo ancho y
# alto), centrado en las puntas internas de las cuatro marcas en T de la hoja de
# calibración y ampliado un 6 % respecto del ancho medido:
# centro (198,303), lado 226 px → TL (85,190), TR (311,190), BR (311,416),
# BL (85,416). El cuadrilátero anterior era 214×158 px (no cuadrado).
#
# IMPORTANTE (I-04): las coordenadas normalizadas solo producen un cuadrado de
# 226×226 px si el frame es **portrait 480×640** (ancho 480, alto 640). La
# calibración se hizo sobre frames de la cámara IP en esa orientación.
# ``CAMERA_WIDTH``/``CAMERA_HEIGHT`` son solo fallback/negociación: si la
# fuente entrega otra resolución (p. ej. 640×480 landscape), el área se vuelve
# un rectángulo (~302×170 px) y hay que ajustar ``SCAN_QUAD`` o reconfigurar la
# cámara. No cambiar los valores por defecto sin recalibrar.
# Se puede sobrescribir con ``SCAN_QUAD="x1,y1,x2,y2,x3,y3,x4,y4"``.
DEFAULT_SCAN_QUAD: tuple[tuple[float, float], ...] = (
    (0.1762, 0.2962),
    (0.6477, 0.2962),
    (0.6477, 0.6499),
    (0.1762, 0.6499),
)


def _parse_scan_quad(raw: str | None) -> tuple[tuple[float, float], ...]:
    """Parsea y valida ``SCAN_QUAD``; ante cualquier valor inválido usa el default.

    Args:
        raw: texto ``"x1,y1,x2,y2,x3,y3,x4,y4"`` normalizado o ``None``.

    Returns:
        Cuatro puntos ``(x, y)`` en orden TL, TR, BR, BL.
    """
    if not raw:
        return DEFAULT_SCAN_QUAD
    try:
        values = tuple(float(part.strip()) for part in raw.split(","))
    except ValueError:
        return DEFAULT_SCAN_QUAD
    if len(values) != 8 or not all(0.0 <= value <= 1.0 for value in values):
        return DEFAULT_SCAN_QUAD
    points = tuple((values[index], values[index + 1]) for index in range(0, 8, 2))
    if len(set(points)) < 4:
        return DEFAULT_SCAN_QUAD
    return points


SCAN_QUAD: tuple[tuple[float, float], ...] = _parse_scan_quad(os.environ.get("SCAN_QUAD"))
NMS_IOU_THRESHOLD: float = 0.30
INSTANCE_CENTER_DISTANCE_PX: float = 40.0
# 1 instancia por plantilla: el visor muestra UNA placa a la vez. Más de 1
# generaba cajas fantasma superpuestas (descriptores sobrantes re-matheados).
MAX_INSTANCES_PER_TEMPLATE: int = 1
# Si el polígono proyectado ocupa menos que esta fracción de su bounding box se
# considera una homografía degenerada y se descarta.
MIN_POLYGON_FILL_RATIO: float = 0.20

# ---------------------------------------------------------------------------
# Plantillas / modo enseñar
# ---------------------------------------------------------------------------
TEMPLATE_MIN_KEYPOINTS: int = 50
TEMPLATE_EXTENSION: str = ".npz"
DEFAULT_LABELS: tuple[str, ...] = (
    "arduino_uno_r4_wifi",
    "raspberry_pi_4",
    "arduino_mkr_wifi_1010",
    "esp32",
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
