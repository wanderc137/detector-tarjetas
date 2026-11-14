"""Captura de video desde la webcam USB (V4L2) con reconexión automática.

El hilo de captura nunca bloquea al resto de la aplicación: publica el último
frame (y una cola de tamaño reducido para el detector) y, si la cámara no está
disponible, genera un frame sintético de estado y reintenta abrirla cada
``CAMERA_RETRY_DELAY_S`` segundos.

El stream MJPEG del servidor consume ``get_frame()`` (último frame, sin
consumir la cola) mientras que el detector consume ``frame_queue``. De esa
forma varios clientes de video y el detector no compiten por los mismos frames.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from typing import Deque, Optional

import cv2
import numpy as np

from . import config

LOGGER = logging.getLogger(__name__)


class CameraCapture(threading.Thread):
    """Hilo que captura frames de una cámara V4L2.

    Atributos públicos:
        frame_queue: cola acotada con los últimos frames para el detector.
        is_open: indica si el dispositivo está abierto en este momento.
        resolution: tupla ``(ancho, alto)`` del último frame válido.
        fps: tasa de frames medida (suavizada).
        last_frame: último frame publicado (o frame de estado si no hay cámara).
    """

    def __init__(
        self,
        index: int = config.CAMERA_INDEX,
        width: int = config.CAMERA_WIDTH,
        height: int = config.CAMERA_HEIGHT,
        fps_target: int = config.CAMERA_FPS_TARGET,
        retry_delay: float = config.CAMERA_RETRY_DELAY_S,
        frame_queue: Optional["queue.Queue[np.ndarray]"] = None,
    ) -> None:
        """Inicializa el hilo de captura.

        Args:
            index: índice del dispositivo de video (``0`` = ``/dev/video0``).
            width: ancho solicitado al driver.
            height: alto solicitado al driver.
            fps_target: FPS solicitados al driver.
            retry_delay: segundos entre intentos de reapertura.
            frame_queue: cola compartida opcional; si no se pasa se crea una
                de tamaño ``config.FRAME_QUEUE_SIZE``.
        """
        super().__init__(name="camera-capture", daemon=True)
        self.index = index
        self.width = width
        self.height = height
        self.fps_target = fps_target
        self.retry_delay = retry_delay

        self.frame_queue: "queue.Queue[np.ndarray]" = (
            frame_queue if frame_queue is not None else queue.Queue(maxsize=config.FRAME_QUEUE_SIZE)
        )

        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._capture: Optional[cv2.VideoCapture] = None
        self._last_frame: Optional[np.ndarray] = None
        self._is_open = False
        self._resolution: tuple[int, int] = (width, height)
        self._fps = 0.0
        self._frame_times: Deque[float] = deque(maxlen=30)
        self._open_attempts = 0

    # ------------------------------------------------------------------
    # Propiedades
    # ------------------------------------------------------------------
    @property
    def is_open(self) -> bool:
        """``True`` si el dispositivo de captura está abierto y entregando frames."""
        with self._lock:
            return self._is_open

    @property
    def resolution(self) -> tuple[int, int]:
        """Resolución actual del stream como ``(ancho, alto)``."""
        with self._lock:
            return self._resolution

    @property
    def fps(self) -> float:
        """FPS medidos (media móvil de los últimos frames)."""
        with self._lock:
            return round(self._fps, 1)

    @property
    def last_frame(self) -> Optional[np.ndarray]:
        """Último frame publicado, o ``None`` si todavía no hay ninguno."""
        with self._lock:
            return self._last_frame

    @property
    def open_attempts(self) -> int:
        """Cantidad de intentos de apertura realizados (útil para diagnóstico)."""
        with self._lock:
            return self._open_attempts

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    def get_frame(self) -> Optional[np.ndarray]:
        """Devuelve el último frame disponible sin bloquear.

        Returns:
            Un ``np.ndarray`` BGR o ``None`` si aún no se publicó ningún frame.
        """
        with self._lock:
            return self._last_frame

    def stop(self) -> None:
        """Señala el cierre del hilo y espera su finalización.

        Primero se le pide al hilo que termine y se espera; solo si sigue
        bloqueado en ``read()`` se libera el ``VideoCapture`` desde afuera para
        desbloquearlo (liberarlo en paralelo con una lectura puede hacer abortar
        al backend V4L2 de OpenCV).
        """
        self._stop_event.set()
        if self.is_alive():
            self.join(timeout=3.0)
        if self.is_alive():
            self._release_capture()
            self.join(timeout=2.0)
        self._release_capture()

    # ------------------------------------------------------------------
    # Hilo principal
    # ------------------------------------------------------------------
    def run(self) -> None:  # noqa: D401 - docstring heredado de Thread
        """Bucle de captura: abre la cámara, lee frames y reintenta si falla."""
        LOGGER.info("Hilo de cámara iniciado (device=%s)", self.index)
        while not self._stop_event.is_set():
            if self._capture is None:
                self._try_open()
                if self._capture is None:
                    self._publish(self.make_status_frame())
                    self._stop_event.wait(self.retry_delay)
                    continue

            try:
                ok, frame = self._capture.read()
            except cv2.error as exc:  # pragma: no cover - depende del driver
                LOGGER.warning("Error leyendo de la cámara: %s", exc)
                ok, frame = False, None

            if not ok or frame is None:
                LOGGER.warning("La cámara dejó de entregar frames; reintentando en %.1fs", self.retry_delay)
                self._release_capture()
                self._publish(self.make_status_frame("CAMARA DESCONECTADA"))
                self._stop_event.wait(self.retry_delay)
                continue

            self._publish(frame)

        self._release_capture()
        LOGGER.info("Hilo de cámara detenido")

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------
    def _try_open(self) -> None:
        """Intenta abrir el dispositivo probando primero el backend V4L2."""
        with self._lock:
            self._open_attempts += 1
            attempt = self._open_attempts

        backends = []
        if hasattr(cv2, "CAP_V4L2"):
            backends.append(("V4L2", cv2.CAP_V4L2))
        backends.append(("default", cv2.CAP_ANY))

        for name, backend in backends:
            if self._stop_event.is_set():
                return
            cap = cv2.VideoCapture(self.index, backend)
            if not cap.isOpened():
                cap.release()
                continue

            try:
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                cap.set(cv2.CAP_PROP_FPS, self.fps_target)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except cv2.error:  # pragma: no cover - drivers incompletos
                LOGGER.debug("El driver no aceptó todos los parámetros de captura")

            ok, frame = cap.read()
            if not ok or frame is None:
                cap.release()
                continue

            with self._lock:
                self._capture = cap
                self._is_open = True
                self._resolution = (int(frame.shape[1]), int(frame.shape[0]))
            LOGGER.info(
                "Cámara abierta con backend %s en %dx%d (intento %d)",
                name,
                frame.shape[1],
                frame.shape[0],
                attempt,
            )
            self._publish(frame)
            return

        with self._lock:
            self._is_open = False
        LOGGER.warning("No se pudo abrir la cámara %s (intento %d)", self.index, attempt)

    def _release_capture(self) -> None:
        """Libera el ``VideoCapture`` si estaba abierto."""
        with self._lock:
            cap = self._capture
            self._capture = None
            self._is_open = False
        if cap is not None:
            try:
                cap.release()
            except cv2.error:  # pragma: no cover - liberación defensiva
                LOGGER.debug("Error liberando la cámara", exc_info=True)

    def _publish(self, frame: np.ndarray) -> None:
        """Publica un frame en ``last_frame`` y en la cola del detector."""
        now = time.monotonic()
        with self._lock:
            self._last_frame = frame
            self._resolution = (int(frame.shape[1]), int(frame.shape[0]))
            self._frame_times.append(now)
            if len(self._frame_times) >= 2:
                span = self._frame_times[-1] - self._frame_times[0]
                if span > 0:
                    self._fps = (len(self._frame_times) - 1) / span

        try:
            self.frame_queue.put_nowait(frame)
        except queue.Full:
            try:
                self.frame_queue.get_nowait()
            except queue.Empty:  # pragma: no cover - carrera benigna
                pass
            try:
                self.frame_queue.put_nowait(frame)
            except queue.Full:  # pragma: no cover - carrera benigna
                pass

    def make_status_frame(self, message: str = "CAMARA NO DISPONIBLE") -> np.ndarray:
        """Genera un frame sintético gris con un mensaje de estado."""
        width, height = self._resolution if self._is_open else (self.width, self.height)
        frame = np.full((height, width, 3), 42, dtype=np.uint8)

        cv2.line(frame, (0, 0), (width, height), (70, 70, 70), 1)
        cv2.line(frame, (width, 0), (0, height), (70, 70, 70), 1)
        cv2.rectangle(frame, (10, 10), (width - 10, height - 10), (90, 90, 90), 2)

        lines = [
            message,
            f"device {self.index} - reintentando cada {self.retry_delay:.0f}s",
            "Revisa el cable USB o el indice CAMERA_INDEX",
        ]
        font = cv2.FONT_HERSHEY_SIMPLEX
        scale = max(0.5, min(1.0, width / 800.0))
        thickness = 2
        total_height = 0
        sizes = []
        for text in lines:
            (tw, th), base = cv2.getTextSize(text, font, scale, thickness)
            sizes.append((tw, th, base))
            total_height += th + base + 10

        y = max(20, (height - total_height) // 2)
        for text, (tw, th, base) in zip(lines, sizes):
            x = max(10, (width - tw) // 2)
            color = (0, 255, 0) if text is lines[0] else (200, 200, 200)
            cv2.putText(frame, text, (x, y + th), font, scale, color, thickness, cv2.LINE_AA)
            y += th + base + 10
        return frame
