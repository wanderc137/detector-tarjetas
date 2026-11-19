"""Modo enseñar: captura y borrado de muestras ORB desde el navegador.

Cada muestra se guarda como ``data/templates/<label>/<timestamp>.npz`` con los
keypoints (serializados), los descriptores ORB y el tamaño de la imagen de
origen. El detector recarga las plantillas en caliente al terminar la captura.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from . import config
from .detector import create_orb, save_template_archive

LOGGER = logging.getLogger(__name__)

LABEL_PATTERN = re.compile(r"^[a-z0-9_]{2,40}$")


class TeachMode:
    """Gestiona las muestras de entrenamiento almacenadas en disco."""

    def __init__(
        self,
        template_dir: Optional[Path] = None,
        min_keypoints: int = config.TEMPLATE_MIN_KEYPOINTS,
    ) -> None:
        """Inicializa el modo enseñar.

        Args:
            template_dir: directorio raíz de plantillas; por defecto
                ``config.TEMPLATE_DIR``.
            min_keypoints: mínimo de keypoints para aceptar una muestra.
        """
        self.template_dir = Path(template_dir) if template_dir is not None else config.TEMPLATE_DIR
        self.min_keypoints = min_keypoints
        self._orb = create_orb()
        self._lock = threading.Lock()
        self.template_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    @staticmethod
    def validate_label(label: str) -> str:
        """Valida el formato de una etiqueta.

        Args:
            label: etiqueta recibida del cliente.

        Returns:
            La etiqueta normalizada (minúsculas, sin espacios).

        Raises:
            ValueError: si la etiqueta no cumple ``^[a-z0-9_]{2,40}$``.
        """
        if not isinstance(label, str):
            raise ValueError("La etiqueta debe ser una cadena de texto")
        normalized = label.strip().lower()
        if not LABEL_PATTERN.match(normalized):
            raise ValueError(
                "Etiqueta inválida: usá entre 2 y 40 caracteres [a-z0-9_] (ej: arduino_uno_r4_wifi)"
            )
        return normalized

    def capture(
        self,
        label: str,
        frame: Optional[np.ndarray],
        camera_open: bool = True,
    ) -> dict[str, object]:
        """Captura una muestra ORB del frame actual y la guarda en disco.

        Args:
            label: etiqueta destino (se valida).
            frame: imagen BGR del frame actual; ``None`` si no hay cámara.
            camera_open: indica si la cámara está entregando imágenes reales.
                Cuando es ``False`` se rechaza la captura aunque ``frame`` no sea
                ``None``, porque el frame sería el sintético de estado.

        Returns:
            Diccionario con ``ok``, ``message``, ``keypoints`` y ``samples``.
        """
        try:
            label = self.validate_label(label)
        except ValueError as exc:
            return {"ok": False, "message": str(exc), "keypoints": 0, "samples": 0}

        if not camera_open:
            return {
                "ok": False,
                "message": (
                    "La cámara no está entregando imágenes reales. "
                    "Conectá la webcam (o revisá /dev/video0) antes de capturar."
                ),
                "keypoints": 0,
                "samples": self.count_samples(label),
            }

        if frame is None:
            return {
                "ok": False,
                "message": "No hay frame disponible: revisá la cámara antes de capturar.",
                "keypoints": 0,
                "samples": self.count_samples(label),
            }

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        keypoints, descriptors = self._orb.detectAndCompute(gray, None)
        count = 0 if keypoints is None else len(keypoints)

        if descriptors is None or count < self.min_keypoints:
            return {
                "ok": False,
                "message": (
                    f"Pocos puntos característicos ({count}/{self.min_keypoints}). "
                    "Acercá la placa, mejorá la luz o evitá fondos lisos."
                ),
                "keypoints": count,
                "samples": self.count_samples(label),
            }

        height, width = gray.shape[:2]
        timestamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
        filename = f"{timestamp}-{int(time.time() * 1000) % 1000:03d}-{uuid.uuid4().hex[:6]}{config.TEMPLATE_EXTENSION}"
        path = self.template_dir / label / filename

        try:
            with self._lock:
                save_template_archive(path, keypoints, descriptors, (width, height), label)
        except OSError as exc:
            LOGGER.exception("No se pudo guardar la muestra %s", path)
            return {
                "ok": False,
                "message": f"No se pudo guardar la muestra: {exc}",
                "keypoints": count,
                "samples": self.count_samples(label),
            }

        samples = self.count_samples(label)
        LOGGER.info("Muestra guardada para '%s': %s (%d keypoints)", label, path.name, count)
        return {
            "ok": True,
            "message": f"Muestra guardada ({count} keypoints). Total: {samples}.",
            "keypoints": count,
            "samples": samples,
            "file": str(path.relative_to(self.template_dir)),
        }

    def reset(self, label: str) -> dict[str, object]:
        """Borra las muestras de una etiqueta o todas.

        Args:
            label: etiqueta a limpiar, o ``"__all__"`` para borrar todo.

        Returns:
            Diccionario con ``ok``, ``message`` y ``removed`` (archivos borrados).
        """
        if label == "__all__":
            removed = 0
            with self._lock:
                if self.template_dir.exists():
                    for label_dir in [p for p in self.template_dir.iterdir() if p.is_dir()]:
                        for path in label_dir.glob(f"*{config.TEMPLATE_EXTENSION}"):
                            try:
                                path.unlink()
                                removed += 1
                            except OSError as exc:  # pragma: no cover - permisos
                                LOGGER.warning("No se pudo borrar %s: %s", path, exc)
            return {
                "ok": True,
                "message": f"Muestras borradas: {removed}.",
                "removed": removed,
            }

        try:
            label = self.validate_label(label)
        except ValueError as exc:
            return {"ok": False, "message": str(exc), "removed": 0}

        removed = 0
        label_dir = self.template_dir / label
        with self._lock:
            if label_dir.is_dir():
                for path in label_dir.glob(f"*{config.TEMPLATE_EXTENSION}"):
                    try:
                        path.unlink()
                        removed += 1
                    except OSError as exc:  # pragma: no cover - permisos
                        LOGGER.warning("No se pudo borrar %s: %s", path, exc)
        return {
            "ok": True,
            "message": f"Muestras borradas de '{label}': {removed}.",
            "removed": removed,
        }

    def list_labels(self) -> list[str]:
        """Lista las etiquetas con al menos una muestra guardada."""
        if not self.template_dir.exists():
            return []
        labels: list[str] = []
        for label_dir in sorted(p for p in self.template_dir.iterdir() if p.is_dir()):
            if any(label_dir.glob(f"*{config.TEMPLATE_EXTENSION}")):
                labels.append(label_dir.name)
        return labels

    def count_samples(self, label: str) -> int:
        """Cuenta las muestras guardadas para una etiqueta."""
        if not LABEL_PATTERN.match(label or ""):
            return 0
        label_dir = self.template_dir / label
        if not label_dir.is_dir():
            return 0
        return sum(1 for _ in label_dir.glob(f"*{config.TEMPLATE_EXTENSION}"))

    def counts(self) -> dict[str, int]:
        """Devuelve ``{label: cantidad}`` incluyendo etiquetas conocidas vacías."""
        counts = {label: self.count_samples(label) for label in config.DEFAULT_LABELS}
        for label in self.list_labels():
            counts.setdefault(label, self.count_samples(label))
        return counts
