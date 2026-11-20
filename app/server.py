"""Servidor HTTP de la aplicación y punto de entrada.

Expone:
    ``GET  /``                   página principal
    ``GET  /teach``              página del modo enseñar
    ``GET  /static/*``           recursos estáticos
    ``GET  /stream.mjpg``        stream MJPEG
    ``GET  /api/detections``     detecciones actuales (JSON)
    ``GET  /api/boards``         fichas técnicas (JSON)
    ``GET  /api/health``         estado de cámara, plantillas y detector
    ``POST /api/teach/capture``  captura una muestra ORB
    ``POST /api/teach/reset``    borra muestras de una etiqueta o todas
    ``POST /api/teach/reload``   recarga plantillas en caliente (extensión)

El servidor usa ``ThreadingHTTPServer`` para no bloquear el stream por culpa
del polling de la API. Los hilos de cámara y detector son daemon y se detienen
ordenadamente con ``SIGINT``/``SIGTERM``.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional, Type
from urllib.parse import unquote, urlsplit

import cv2
import numpy as np

from . import __version__, config
from .camera import CameraCapture
from .detector import BoardDetector
from .specs import BoardSpecs
from .teach import TeachMode

LOGGER = logging.getLogger(__name__)

MIME_TYPES: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".webp": "image/webp",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
}


def encode_jpeg(frame: np.ndarray, quality: int = config.JPEG_QUALITY) -> Optional[bytes]:
    """Codifica un frame BGR como JPEG.

    Args:
        frame: imagen BGR a codificar.
        quality: calidad JPEG (0-100).

    Returns:
        Bytes del JPEG o ``None`` si OpenCV no pudo codificar el frame.
    """
    try:
        ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    except cv2.error:  # pragma: no cover - defensivo
        LOGGER.warning("cv2.imencode falló", exc_info=True)
        return None
    if not ok:
        return None
    return buffer.tobytes()


class DetectorHTTPServer(ThreadingHTTPServer):
    """Servidor HTTP con backlog ampliado y cierre limpio de conexiones."""

    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 32


class DetectorTarjetasApp:
    """Contenedor de los componentes de la aplicación (cámara, detector, datos)."""

    def __init__(
        self,
        boards_path: Optional[Path] = None,
        template_dir: Optional[Path] = None,
        camera_index: Optional[int] = None,
        camera: Optional[Any] = None,
        interval_ms: int = config.DETECTOR_INTERVAL_MS,
    ) -> None:
        """Construye la aplicación sin arrancar hilos.

        Args:
            boards_path: ruta alternativa a ``boards.json`` (tests).
            template_dir: directorio alternativo de plantillas (tests).
            camera_index: índice de cámara; por defecto ``config.CAMERA_INDEX``.
            camera: implementación de cámara inyectable (tests); debe exponer
                ``frame_queue``, ``is_open``, ``resolution``, ``fps``,
                ``start()``, ``stop()`` y ``get_frame()``.
            interval_ms: intervalo mínimo entre inferencias.
        """
        self.boards_path = Path(boards_path) if boards_path is not None else config.BOARDS_JSON
        self.template_dir = Path(template_dir) if template_dir is not None else config.TEMPLATE_DIR

        self.specs = BoardSpecs(self.boards_path)
        if camera is not None:
            self.camera = camera
        else:
            if camera_index is not None:
                source: int | str = camera_index
            else:
                source = config.load_camera_source()
            self.camera = CameraCapture(source=source)

        self.detector = BoardDetector(
            frame_queue=self.camera.frame_queue,
            template_dir=self.template_dir,
            interval_ms=interval_ms,
        )
        self.teach = TeachMode(template_dir=self.template_dir)

        self.stop_event = threading.Event()
        self._started_at = time.time()
        self._started = False

    # ------------------------------------------------------------------
    def start(self) -> None:
        """Arranca los hilos de cámara y detector."""
        if self._started:
            return
        self._started = True
        self.detector.reload_templates()
        self.camera.start()
        self.detector.start()
        LOGGER.info(
            "Aplicación iniciada (cámara=%s, plantillas=%s)",
            getattr(self.camera, "index", "?"),
            self.detector.template_counts or "ninguna",
        )

    def stop(self) -> None:
        """Detiene los hilos y libera la cámara."""
        self.stop_event.set()
        try:
            self.detector.stop()
        finally:
            self.camera.stop()
        self._started = False
        LOGGER.info("Aplicación detenida")

    # ------------------------------------------------------------------
    def boards_payload(self) -> list[dict[str, Any]]:
        """Serializa todas las fichas técnicas para ``/api/boards``."""
        payload: list[dict[str, Any]] = []
        for spec in self.specs.get_all():
            item = spec.to_dict()
            item["formatted"] = spec.formatted()
            item["summary"] = self.specs.get_summary(spec.id)
            payload.append(item)
        return payload

    def detection_payload(self) -> dict[str, Any]:
        """Serializa las detecciones actuales enriquecidas con datos de la ficha."""
        boards: dict[str, Any] = {}
        try:
            boards = {spec.id: spec for spec in self.specs.get_all()}
        except (OSError, ValueError) as exc:
            LOGGER.warning("No se pudieron cargar las fichas para las detecciones: %s", exc)

        items: list[dict[str, Any]] = []
        for detection in self.detector.get_detections():
            item = detection.to_dict()
            spec = boards.get(detection.label)
            if spec is not None:
                item["name"] = spec.name
                item["short_name"] = spec.short_name
                item["summary"] = self.specs.get_summary(detection.label)
            else:
                item["name"] = detection.label
                item["short_name"] = detection.label
                item["summary"] = {}
            items.append(item)

        return {
            "timestamp": round(time.time(), 3),
            "detections": items,
        }

    def health_payload(self) -> dict[str, Any]:
        """Estado del sistema para ``/api/health``."""
        counts = self.teach.counts()
        labels_with_samples = sorted(label for label, count in counts.items() if count > 0)
        return {
            "status": "ok",
            "camera": {
                "present": bool(getattr(self.camera, "is_open", False)),
                "resolution": list(getattr(self.camera, "resolution", (config.CAMERA_WIDTH, config.CAMERA_HEIGHT))),
                "fps": float(getattr(self.camera, "fps", 0.0)),
                "index": getattr(self.camera, "source", getattr(self.camera, "index", config.CAMERA_INDEX)),
            },
            "scan_quad": [
                [float(x), float(y)] for x, y in config.SCAN_QUAD
            ],
            "templates": {
                "count": int(sum(counts.values())),
                "labels": labels_with_samples,
                "by_label": counts,
                "loaded": self.detector.template_counts,
            },
            "detector": {
                "last_inference_ms": self.detector.last_inference_ms,
                "inferences": self.detector.inference_count,
            },
            "uptime_s": round(time.time() - self._started_at, 1),
            "version": __version__,
        }

    def set_camera_source(self, url: str) -> str:
        """Persiste y aplica una nueva URL de cámara en caliente.

        Args:
            url: URL del stream (p.ej. ``http://192.168.1.8:8080/video``).

        Returns:
            La URL normalizada (sin espacios) que quedó aplicada.
        """
        url = url.strip()
        config.CAMERA_URL_FILE.write_text(url + "\n", encoding="utf-8")
        self.camera.set_source(url)
        return url

    # ------------------------------------------------------------------
    def build_server(self, host: str = config.SERVER_HOST, port: int = config.SERVER_PORT) -> ThreadingHTTPServer:
        """Crea el servidor HTTP listo para ``serve_forever``.

        Args:
            host: interfaz de escucha.
            port: puerto; ``0`` elige uno libre (útil en tests).

        Returns:
            Servidor HTTP configurado.
        """
        handler_class = make_handler(self)
        return DetectorHTTPServer((host, port), handler_class)


# ---------------------------------------------------------------------------
# Handler HTTP
# ---------------------------------------------------------------------------
def make_handler(app: DetectorTarjetasApp) -> Type[BaseHTTPRequestHandler]:
    """Crea la clase de handler asociada a una instancia de la aplicación."""

    class Handler(BaseHTTPRequestHandler):
        """Handler HTTP con rutas manuales (sin frameworks)."""

        server_version = "DetectorTarjetas/" + __version__
        protocol_version = "HTTP/1.1"

        # ------------------------------------------------------------------
        # Helpers de respuesta
        # ------------------------------------------------------------------
        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: D102 - override
            LOGGER.debug("%s - %s", self.address_string(), fmt % args)

        def _add_cors(self) -> None:
            self.send_header("Access-Control-Allow-Origin", config.CORS_ALLOW_ORIGIN)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def _send_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(int(status))
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self._add_cors()
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):  # pragma: no cover
                LOGGER.debug("Cliente desconectado antes de recibir el JSON")

        def _send_error_json(self, status: int, message: str, detail: str = "") -> None:
            payload: dict[str, Any] = {"ok": False, "error": message}
            if detail:
                payload["detail"] = detail
            self._send_json(payload, status=status)

        def _send_bytes(self, body: bytes, content_type: str, status: int = HTTPStatus.OK, cache: bool = False) -> None:
            self.send_response(int(status))
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header(
                "Cache-Control", "public, max-age=3600" if cache else "no-store"
            )
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):  # pragma: no cover
                LOGGER.debug("Cliente desconectado antes de recibir el archivo")

        def _read_json_body(self) -> Optional[dict[str, Any]]:
            """Lee y parsea el cuerpo JSON; devuelve ``None`` si es inválido."""
            length_header = self.headers.get("Content-Length")
            if length_header is None:
                self._send_error_json(HTTPStatus.BAD_REQUEST, "Falta el header Content-Length")
                return None
            try:
                length = int(length_header)
            except (TypeError, ValueError):
                self._send_error_json(HTTPStatus.BAD_REQUEST, "Content-Length inválido")
                return None
            if length <= 0 or length > config.MAX_JSON_BODY_BYTES:
                self._send_error_json(HTTPStatus.BAD_REQUEST, "Cuerpo JSON vacío o demasiado grande")
                return None
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, "JSON inválido", str(exc))
                return None
            if not isinstance(payload, dict):
                self._send_error_json(HTTPStatus.BAD_REQUEST, "El cuerpo debe ser un objeto JSON")
                return None
            return payload

        # ------------------------------------------------------------------
        # Rutas
        # ------------------------------------------------------------------
        def do_OPTIONS(self) -> None:  # noqa: N802 - API de BaseHTTPRequestHandler
            self.send_response(HTTPStatus.NO_CONTENT)
            self._add_cors()
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802 - API de BaseHTTPRequestHandler
            path = unquote(urlsplit(self.path).path)
            try:
                if path in ("/", "/index.html"):
                    self._serve_page("index.html")
                elif path in ("/teach", "/teach/", "/teach.html"):
                    self._serve_page("teach.html")
                elif path.startswith("/static/"):
                    self._serve_static(path[len("/static/") :])
                elif path == "/stream.mjpg":
                    self._serve_stream()
                elif path == "/api/detections":
                    self._send_json(self.app.detection_payload())
                elif path == "/api/boards":
                    self._send_json(self.app.boards_payload())
                elif path == "/api/health":
                    self._send_json(self.app.health_payload())
                elif path == "/api/teach/samples":
                    self._send_json({"counts": self.app.teach.counts()})
                elif path == "/favicon.ico":
                    self.send_response(HTTPStatus.NO_CONTENT)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                else:
                    self._send_error_json(HTTPStatus.NOT_FOUND, "Ruta no encontrada", path)
            except (BrokenPipeError, ConnectionResetError):  # pragma: no cover
                LOGGER.debug("Cliente desconectado durante GET %s", path)
            except Exception:  # pragma: no cover - red de seguridad
                LOGGER.exception("Error no controlado en GET %s", path)
                try:
                    self._send_error_json(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        "Error interno del servidor",
                        "Revisá los logs del servicio",
                    )
                except (BrokenPipeError, ConnectionResetError):
                    pass

        def do_POST(self) -> None:  # noqa: N802 - API de BaseHTTPRequestHandler
            path = unquote(urlsplit(self.path).path)
            try:
                if path == "/api/teach/capture":
                    self._handle_capture()
                elif path == "/api/teach/reset":
                    self._handle_reset()
                elif path == "/api/teach/reload":
                    self._handle_reload()
                elif path == "/api/camera":
                    self._handle_set_camera()
                else:
                    self._send_error_json(HTTPStatus.NOT_FOUND, "Ruta no encontrada", path)
            except (BrokenPipeError, ConnectionResetError):  # pragma: no cover
                LOGGER.debug("Cliente desconectado durante POST %s", path)
            except Exception:  # pragma: no cover - red de seguridad
                LOGGER.exception("Error no controlado en POST %s", path)
                try:
                    self._send_error_json(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        "Error interno del servidor",
                        "Revisá los logs del servicio",
                    )
                except (BrokenPipeError, ConnectionResetError):
                    pass

        # ------------------------------------------------------------------
        # Handlers específicos
        # ------------------------------------------------------------------
        def _handle_capture(self) -> None:
            payload = self._read_json_body()
            if payload is None:
                return
            label = payload.get("label", "")
            # Sin cámara abierta el frame disponible es el sintético de estado
            # («CÁMARA NO DISPONIBLE»): no debe usarse como muestra de teach.
            camera_open = bool(getattr(self.app.camera, "is_open", False))
            frame = self.app.camera.get_frame() if camera_open else None
            result = self.app.teach.capture(label, frame, camera_open=camera_open)
            if result.get("ok"):
                counts = self.app.detector.reload_templates()
                result["loaded"] = counts
                self._send_json(result, status=HTTPStatus.OK)
            else:
                self._send_json(result, status=HTTPStatus.UNPROCESSABLE_ENTITY)

        def _handle_reset(self) -> None:
            payload = self._read_json_body()
            if payload is None:
                return
            label = payload.get("label", "")
            if not isinstance(label, str) or not label:
                self._send_error_json(HTTPStatus.BAD_REQUEST, "Falta el campo 'label'")
                return
            result = self.app.teach.reset(label)
            if result.get("ok"):
                counts = self.app.detector.reload_templates()
                result["loaded"] = counts
                self._send_json(result, status=HTTPStatus.OK)
            else:
                self._send_json(result, status=HTTPStatus.BAD_REQUEST)

        def _handle_reload(self) -> None:
            # La recarga en caliente es un POST simple: el body es opcional.
            # Si el cliente envió uno (p. ej. ``{}``) se consume para no romper
            # la conexión keep-alive, respetando el límite de tamaño.
            length_header = self.headers.get("Content-Length")
            if length_header is not None:
                try:
                    length = int(length_header)
                except (TypeError, ValueError):
                    self._send_error_json(HTTPStatus.BAD_REQUEST, "Content-Length inválido")
                    return
                if length < 0 or length > config.MAX_JSON_BODY_BYTES:
                    self._send_error_json(
                        HTTPStatus.BAD_REQUEST, "Cuerpo demasiado grande"
                    )
                    return
                if length:
                    try:
                        self.rfile.read(length)
                    except (OSError, ValueError):  # pragma: no cover - cliente cortado
                        LOGGER.debug("No se pudo consumir el body de /api/teach/reload")

            counts = self.app.detector.reload_templates()
            self._send_json({"ok": True, "counts": counts, "samples": self.app.teach.counts()})

        def _handle_set_camera(self) -> None:
            payload = self._read_json_body()
            if payload is None:
                return
            url = payload.get("url", "")
            if not isinstance(url, str) or not url.strip():
                self._send_error_json(HTTPStatus.BAD_REQUEST, "Falta el campo 'url'")
                return
            url = url.strip()
            if not (url.startswith("http://") or url.startswith("https://") or url.startswith("rtsp://")):
                self._send_error_json(
                    HTTPStatus.BAD_REQUEST,
                    "La URL debe empezar con http://, https:// o rtsp://",
                )
                return
            try:
                applied = self.app.set_camera_source(url)
            except OSError as exc:
                self._send_error_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "No se pudo guardar la URL de cámara",
                    str(exc),
                )
                return
            self._send_json({"ok": True, "source": applied})

        # ------------------------------------------------------------------
        # Archivos y stream
        # ------------------------------------------------------------------
        def _serve_page(self, filename: str) -> None:
            path = (config.WEB_DIR / filename).resolve()
            if not path.is_file():
                self._send_error_json(HTTPStatus.NOT_FOUND, "Página no encontrada", filename)
                return
            self._send_bytes(path.read_bytes(), "text/html; charset=utf-8")

        def _serve_static(self, relative: str) -> None:
            relative = relative.split("?", 1)[0]
            base = config.STATIC_DIR.resolve()
            target = (base / relative).resolve()
            if base != target and base not in target.parents:
                self._send_error_json(HTTPStatus.FORBIDDEN, "Ruta estática inválida")
                return
            if not target.is_file():
                self._send_error_json(HTTPStatus.NOT_FOUND, "Recurso no encontrado", relative)
                return
            content_type = MIME_TYPES.get(target.suffix.lower(), "application/octet-stream")
            self._send_bytes(target.read_bytes(), content_type, cache=False)

        def _serve_stream(self) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", config.STREAM_CONTENT_TYPE)
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()

            frame_interval = 1.0 / max(1, config.STREAM_MAX_FPS)
            placeholder = self.app.camera.make_status_frame()
            LOGGER.info("Cliente conectado al stream MJPEG")

            try:
                while not self.app.stop_event.is_set():
                    started = time.monotonic()
                    frame = self.app.camera.get_frame()
                    if frame is None:
                        frame = placeholder
                    data = encode_jpeg(frame)
                    if data:
                        self.wfile.write(b"--frame\r\n")
                        self.wfile.write(b"Content-Type: image/jpeg\r\n")
                        self.wfile.write(f"Content-Length: {len(data)}\r\n\r\n".encode("ascii"))
                        self.wfile.write(data)
                        self.wfile.write(b"\r\n")
                        self.wfile.flush()
                    elapsed = time.monotonic() - started
                    if elapsed < frame_interval:
                        time.sleep(frame_interval - elapsed)
            except (BrokenPipeError, ConnectionResetError, OSError):
                LOGGER.debug("Cliente de stream desconectado")
            finally:
                self.close_connection = True
                LOGGER.info("Cliente desconectado del stream MJPEG")

    Handler.app = app  # type: ignore[attr-defined]
    return Handler


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------
def main() -> int:
    """Arranca el servidor y espera señales de cierre.

    Returns:
        Código de salida del proceso.
    """
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s",
    )

    app = DetectorTarjetasApp()
    try:
        server = app.build_server(config.SERVER_HOST, config.SERVER_PORT)
    except OSError as exc:
        LOGGER.error(
            "No se pudo abrir %s:%d (%s). ¿Otro proceso usa ese puerto? Probá PORT=8010.",
            config.SERVER_HOST,
            config.SERVER_PORT,
            exc,
        )
        return 1

    try:
        app.start()
    except (OSError, ValueError) as exc:
        LOGGER.error("No se pudo iniciar la aplicación: %s", exc)
        server.server_close()
        return 1

    LOGGER.info(
        "Servidor escuchando en http://%s:%d (stream: /stream.mjpg)",
        config.SERVER_HOST,
        config.SERVER_PORT,
    )

    def _shutdown(signum: int, _frame: Any) -> None:
        LOGGER.info("Señal %s recibida; cerrando...", signal.Signals(signum).name)
        threading.Thread(target=server.shutdown, name="shutdown", daemon=True).start()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:  # pragma: no cover - cubierto por SIGINT
        LOGGER.info("Interrumpido por el usuario")
    finally:
        server.server_close()
        app.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
