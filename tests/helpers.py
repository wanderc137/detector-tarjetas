"""Utilidades compartidas por los tests: imágenes sintéticas y cámara falsa."""

from __future__ import annotations

import queue
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from app.detector import create_orb, save_template_archive


def make_texture(
    seed: int = 7, width: int = 420, height: int = 300, background: int = 18
) -> np.ndarray:
    """Genera una imagen BGR sintética con textura rica en esquinas y texto.

    Args:
        seed: semilla para reproducibilidad.
        width: ancho de la imagen.
        height: alto de la imagen.
        background: nivel de gris del fondo.

    Returns:
        Imagen BGR ``(height, width, 3)`` uint8.
    """
    rng = np.random.default_rng(seed)
    image = np.full((height, width, 3), background, dtype=np.uint8)

    for _ in range(90):
        color = tuple(int(value) for value in rng.integers(90, 256, size=3))
        x = int(rng.integers(2, width - 2))
        y = int(rng.integers(2, height - 2))
        kind = int(rng.integers(0, 3))
        if kind == 0:
            radius = int(rng.integers(3, 14))
            cv2.circle(image, (x, y), radius, color, -1, cv2.LINE_AA)
        elif kind == 1:
            x2 = min(width - 1, x + int(rng.integers(6, 46)))
            y2 = min(height - 1, y + int(rng.integers(6, 36)))
            cv2.rectangle(image, (x, y), (x2, y2), color, -1)
        else:
            x2 = int(rng.integers(0, width))
            y2 = int(rng.integers(0, height))
            cv2.line(image, (x, y), (x2, y2), color, int(rng.integers(1, 4)), cv2.LINE_AA)

    cv2.putText(image, "UNO R4", (18, height - 26), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (255, 255, 255), 3, cv2.LINE_AA)
    cv2.putText(image, "RA4M1", (26, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (230, 230, 230), 2, cv2.LINE_AA)
    cv2.putText(image, "ARDUINO", (26, 74), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (120, 220, 255), 2, cv2.LINE_AA)
    return image


def write_template(
    root: Path, label: str, image: np.ndarray, name: str = "sample.npz"
) -> Path:
    """Extrae ORB de una imagen y guarda la plantilla ``.npz``.

    Args:
        root: directorio raíz de plantillas.
        label: etiqueta de la muestra.
        image: imagen BGR de origen.
        name: nombre del archivo ``.npz``.

    Returns:
        Ruta del archivo escrito.
    """
    orb = create_orb()
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    keypoints, descriptors = orb.detectAndCompute(gray, None)
    if descriptors is None or not keypoints:
        raise RuntimeError("La imagen sintética no produjo keypoints ORB")
    path = root / label / name
    save_template_archive(path, keypoints, descriptors, (image.shape[1], image.shape[0]), label)
    return path


def make_scene(width: int = 640, height: int = 480, background: int = 26) -> np.ndarray:
    """Crea un fondo oscuro uniforme del tamaño del stream."""
    return np.full((height, width, 3), background, dtype=np.uint8)


def place_texture(
    scene: np.ndarray,
    texture: np.ndarray,
    center: tuple[int, int],
    scale: float = 0.8,
    angle: float = 6.0,
    brightness: float = 1.0,
) -> np.ndarray:
    """Ubica la textura en la escena con rotación, escala y brillo.

    Args:
        scene: imagen destino (se modifica in-place y se retorna).
        texture: imagen de la placa.
        center: centro ``(x, y)`` en píxeles de la escena.
        scale: factor de escala.
        angle: rotación en grados.
        brightness: multiplicador de brillo.

    Returns:
        La escena modificada.
    """
    height, width = texture.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2.0, height / 2.0), angle, scale)
    matrix[0, 2] += center[0] - width / 2.0
    matrix[1, 2] += center[1] - height / 2.0

    warped = cv2.warpAffine(
        texture, matrix, (scene.shape[1], scene.shape[0]), borderValue=(0, 0, 0)
    )
    mask = cv2.warpAffine(
        np.full((height, width), 255, dtype=np.uint8),
        matrix,
        (scene.shape[1], scene.shape[0]),
    )
    if brightness != 1.0:
        warped = np.clip(warped.astype(np.float32) * brightness, 0, 255).astype(np.uint8)
    scene[mask > 0] = warped[mask > 0]
    return scene


class FakeCamera:
    """Cámara de prueba con frame fijo (no abre /dev/video*)."""

    def __init__(
        self, frame: Optional[np.ndarray] = None, present: bool = True, index: int = 0
    ) -> None:
        self.index = index
        self.frame_queue: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=2)
        self.is_open = present
        self.resolution = (
            (frame.shape[1], frame.shape[0]) if frame is not None else (640, 480)
        )
        self.fps = 12.5
        self._frame = frame

    def start(self) -> None:
        """No-op: el frame es fijo."""

    def stop(self) -> None:
        """No-op."""

    def get_frame(self) -> Optional[np.ndarray]:
        """Devuelve el frame configurado."""
        return self._frame

    def make_status_frame(self, message: str = "CAMARA NO DISPONIBLE") -> np.ndarray:
        """Frame sintético de estado, equivalente al de ``CameraCapture``."""
        frame = np.full((480, 640, 3), 42, dtype=np.uint8)
        cv2.putText(
            frame,
            message,
            (60, 240),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.9,
            (0, 255, 0),
            2,
            cv2.LINE_AA,
        )
        return frame
