"""Tests de la API HTTP y del servidor (CT03, CT08–CT15)."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Iterator

import pytest
import requests

from app.server import DetectorTarjetasApp
from tests.helpers import (
    FakeCamera,
    make_scene,
    make_texture,
    place_texture,
    write_template,
)

PROJECT_DIR = Path(__file__).resolve().parents[1]
BOARDS_JSON = PROJECT_DIR / "data" / "boards.json"

REQUIRED_BOARD_FIELDS = (
    "id",
    "name",
    "short_name",
    "mcu_soc",
    "clock_mhz",
    "ram_mb",
    "flash_storage_gb",
    "gpio_count",
    "voltage_v",
    "connectivity",
    "dimensions_mm",
    "year",
    "description",
)


def _build_frame() -> "object":
    """Escena sintética con una placa, usada por la cámara falsa."""
    texture = make_texture()
    scene = make_scene()
    place_texture(scene, texture, center=(320, 240), scale=0.85, angle=5.0)
    return scene


def _wait_for_detection(harness: "ServerHarness", timeout: float = 5.0) -> dict | None:
    """Espera a que el hilo del detector publique al menos una detección.

    Args:
        harness: servidor de prueba en ejecución.
        timeout: tiempo máximo de espera en segundos.

    Returns:
        La primera detección publicada o ``None`` si se agotó el tiempo.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = requests.get(f"{harness.base_url}/api/detections", timeout=5).json()
        if payload["detections"]:
            return payload["detections"][0]
        time.sleep(0.05)
    return None


class ServerHarness:
    """Servidor HTTP en un hilo con puerto efímero, para tests."""

    def __init__(self, tmp_path: Path, frame=None, present: bool = True) -> None:
        self.camera = FakeCamera(frame=frame, present=present)
        self.app = DetectorTarjetasApp(
            boards_path=BOARDS_JSON,
            template_dir=tmp_path / "templates",
            camera=self.camera,
            interval_ms=50,
        )
        self.app.start()
        self.httpd = self.app.build_server("127.0.0.1", 0)
        self.port = int(self.httpd.server_address[1])
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(
            target=self.httpd.serve_forever,
            kwargs={"poll_interval": 0.05},
            name="test-http",
            daemon=True,
        )
        self.thread.start()

    def close(self) -> None:
        """Apaga el servidor y los hilos de la aplicación."""
        self.httpd.shutdown()
        self.httpd.server_close()
        self.app.stop()
        self.thread.join(timeout=2.0)

    def __enter__(self) -> "ServerHarness":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


@pytest.fixture()
def server(tmp_path: Path) -> Iterator[ServerHarness]:
    """Servidor con cámara presente y frame sintético."""
    harness = ServerHarness(tmp_path, frame=_build_frame(), present=True)
    try:
        yield harness
    finally:
        harness.close()


# ---------------------------------------------------------------------------
# CT03 / CT08 / CT09
# ---------------------------------------------------------------------------
def test_boards_endpoint(server: ServerHarness) -> None:
    """CT03/CAT-01: ``/api/boards`` devuelve las 4 fichas en orden canónico."""
    response = requests.get(f"{server.base_url}/api/boards", timeout=5)
    assert response.status_code == 200
    boards = response.json()
    assert isinstance(boards, list)
    assert len(boards) == 4
    assert [board["id"] for board in boards] == [
        "arduino_uno_r4_wifi",
        "raspberry_pi_4",
        "arduino_mkr_wifi_1010",
        "esp32",
    ]
    for board in boards:
        for field in REQUIRED_BOARD_FIELDS:
            assert field in board, f"Falta {field} en {board.get('id')}"
        assert "formatted" in board
        assert "summary" in board


def test_health_endpoint_with_camera(server: ServerHarness) -> None:
    """CT08: ``/api/health`` responde 200 con estado ok y cámara presente."""
    payload = requests.get(f"{server.base_url}/api/health", timeout=5).json()
    assert payload["status"] == "ok"
    assert payload["camera"]["present"] is True
    assert payload["camera"]["resolution"] == [640, 480]
    assert payload["scan_quad"] == [
        [0.1762, 0.2962],
        [0.6477, 0.2962],
        [0.6477, 0.6499],
        [0.1762, 0.6499],
    ]
    assert payload["templates"]["count"] == 0
    assert set(payload["templates"]["by_label"]).issuperset(
        {"arduino_uno_r4_wifi", "raspberry_pi_4", "arduino_mkr_wifi_1010", "esp32"}
    )
    assert "last_inference_ms" in payload["detector"]


def test_detections_endpoint_shape(server: ServerHarness) -> None:
    """CT09: ``/api/detections`` responde JSON con cajas normalizadas."""
    payload = requests.get(f"{server.base_url}/api/detections", timeout=5).json()
    assert "timestamp" in payload
    assert isinstance(payload["detections"], list)
    for detection in payload["detections"]:
        box = detection["box"]
        for key in ("x", "y", "w", "h"):
            assert 0.0 <= box[key] <= 1.0
        assert 0.0 <= detection["score"] <= 1.0
        assert isinstance(detection["instance_id"], int)


# ---------------------------------------------------------------------------
# CT10 / CT11 y recarga en caliente
# ---------------------------------------------------------------------------
def test_teach_capture_creates_sample(server: ServerHarness) -> None:
    """CT10: capturar con etiqueta válida crea un ``.npz`` y actualiza el detector."""
    response = requests.post(
        f"{server.base_url}/api/teach/capture",
        json={"label": "arduino_uno_r4_wifi"},
        timeout=5,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True
    assert payload["keypoints"] >= 50
    assert payload["samples"] == 1

    files = list((server.app.template_dir / "arduino_uno_r4_wifi").glob("*.npz"))
    assert len(files) == 1

    health = requests.get(f"{server.base_url}/api/health", timeout=5).json()
    assert health["templates"]["count"] == 1
    assert health["templates"]["labels"] == ["arduino_uno_r4_wifi"]
    assert health["templates"]["loaded"]["arduino_uno_r4_wifi"] == 1


def test_teach_reset_removes_samples(server: ServerHarness) -> None:
    """CT11: borrar una etiqueta elimina todos sus archivos."""
    requests.post(
        f"{server.base_url}/api/teach/capture",
        json={"label": "arduino_mkr_wifi_1010"},
        timeout=5,
    )
    assert list((server.app.template_dir / "arduino_mkr_wifi_1010").glob("*.npz"))

    response = requests.post(
        f"{server.base_url}/api/teach/reset",
        json={"label": "arduino_mkr_wifi_1010"},
        timeout=5,
    )
    assert response.status_code == 200
    assert response.json()["removed"] >= 1
    assert not list((server.app.template_dir / "arduino_mkr_wifi_1010").glob("*.npz"))

    health = requests.get(f"{server.base_url}/api/health", timeout=5).json()
    assert health["templates"]["count"] == 0


def test_teach_reset_all(server: ServerHarness) -> None:
    """``__all__`` borra las muestras de todas las etiquetas."""
    for label in ("arduino_uno_r4_wifi", "raspberry_pi_4"):
        requests.post(
            f"{server.base_url}/api/teach/capture", json={"label": label}, timeout=5
        )
    response = requests.post(
        f"{server.base_url}/api/teach/reset", json={"label": "__all__"}, timeout=5
    )
    assert response.status_code == 200
    assert response.json()["removed"] >= 2
    assert not list(server.app.template_dir.glob("*/*.npz"))


def test_teach_capture_invalid_label(server: ServerHarness) -> None:
    """Una etiqueta inválida se rechaza con 422 y mensaje claro."""
    response = requests.post(
        f"{server.base_url}/api/teach/capture",
        json={"label": "etiqueta con espacios"},
        timeout=5,
    )
    assert response.status_code == 422
    assert response.json()["ok"] is False
    assert "inválida" in response.json()["message"]


def test_teach_reload_endpoint(server: ServerHarness) -> None:
    """La recarga en caliente responde con el estado de plantillas."""
    response = requests.post(f"{server.base_url}/api/teach/reload", json={}, timeout=5)
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert "counts" in payload


def test_teach_reload_accepts_empty_body(server: ServerHarness) -> None:
    """I-03: un POST sin body (``Content-Length: 0``) también recarga."""
    response = requests.post(f"{server.base_url}/api/teach/reload", timeout=5)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True
    assert "counts" in payload
    assert "samples" in payload


def test_teach_capture_without_camera(tmp_path: Path) -> None:
    """I-02: sin cámara la captura devuelve 422 y no crea archivos."""
    with ServerHarness(tmp_path, frame=None, present=False) as harness:
        response = requests.post(
            f"{harness.base_url}/api/teach/capture",
            json={"label": "arduino_uno_r4_wifi"},
            timeout=5,
        )
        assert response.status_code == 422
        assert "cámara" in response.json()["message"].lower()
        assert not list(harness.app.template_dir.glob("*/*.npz"))


def test_teach_capture_rejects_synthetic_status_frame(tmp_path: Path) -> None:
    """I-02: el frame sintético de estado no se guarda como muestra."""
    status_frame = FakeCamera(present=False).make_status_frame()
    with ServerHarness(tmp_path, frame=status_frame, present=False) as harness:
        response = requests.post(
            f"{harness.base_url}/api/teach/capture",
            json={"label": "arduino_uno_r4_wifi"},
            timeout=5,
        )
        assert response.status_code == 422
        assert "cámara" in response.json()["message"].lower()
        assert not list(harness.app.template_dir.glob("*/*.npz"))


# ---------------------------------------------------------------------------
# CT12 / CT13 y páginas
# ---------------------------------------------------------------------------
def test_stream_mjpeg(server: ServerHarness) -> None:
    """CT12: el stream responde multipart y entrega al menos un frame JPEG."""
    with requests.get(f"{server.base_url}/stream.mjpg", stream=True, timeout=5) as response:
        assert response.status_code == 200
        assert "multipart/x-mixed-replace" in response.headers["Content-Type"]

        data = b""
        for chunk in response.iter_content(chunk_size=1024):
            data += chunk
            if b"--frame" in data and b"Content-Type: image/jpeg" in data and len(data) > 600:
                break
        response.close()

    assert b"--frame" in data
    assert b"Content-Type: image/jpeg" in data
    assert b"\xff\xd8" in data  # SOI de JPEG


def test_static_css(server: ServerHarness) -> None:
    """CT13: ``/static/style.css`` se sirve con el MIME correcto."""
    response = requests.get(f"{server.base_url}/static/style.css", timeout=5)
    assert response.status_code == 200
    assert "text/css" in response.headers["Content-Type"]
    assert "--green" in response.text


def test_index_and_teach_pages(server: ServerHarness) -> None:
    """Las páginas principales responden HTML en español."""
    index = requests.get(f"{server.base_url}/", timeout=5)
    assert index.status_code == 200
    assert "Detector de tarjetas" in index.text
    assert "modo enseñar" in index.text.lower()

    teach = requests.get(f"{server.base_url}/teach", timeout=5)
    assert teach.status_code == 200
    assert "Modo enseñar" in teach.text
    assert "Capturar muestra" in teach.text


def test_unknown_route_returns_404(server: ServerHarness) -> None:
    """Las rutas desconocidas devuelven 404 en JSON."""
    response = requests.get(f"{server.base_url}/no-existe", timeout=5)
    assert response.status_code == 404
    assert response.json()["ok"] is False


# ---------------------------------------------------------------------------
# CT14 / CT15
# ---------------------------------------------------------------------------
def test_health_without_camera(tmp_path: Path) -> None:
    """CT14: arrancar sin cámara no crashea y ``camera.present`` es False."""
    with ServerHarness(tmp_path, frame=None, present=False) as harness:
        response = requests.get(f"{harness.base_url}/api/health", timeout=5)
        assert response.status_code == 200
        payload = response.json()
        assert payload["camera"]["present"] is False
        assert payload["templates"]["count"] == 0


def test_health_without_templates(tmp_path: Path) -> None:
    """CT15: sin plantillas el detector está deshabilitado pero la API responde."""
    with ServerHarness(tmp_path, frame=_build_frame(), present=True) as harness:
        payload = requests.get(f"{harness.base_url}/api/health", timeout=5).json()
        assert payload["templates"]["count"] == 0
        assert payload["templates"]["labels"] == []
        assert payload["templates"]["loaded"] == {}


def test_detections_with_trained_template(server: ServerHarness) -> None:
    """Al capturar una muestra el detector la carga (aunque el frame no cambie)."""
    requests.post(
        f"{server.base_url}/api/teach/capture",
        json={"label": "arduino_uno_r4_wifi"},
        timeout=5,
    )
    health = requests.get(f"{server.base_url}/api/health", timeout=5).json()
    assert health["templates"]["loaded"]["arduino_uno_r4_wifi"] == 1


def test_detection_label_matches_board_spec(tmp_path: Path) -> None:
    """I-01: el label recargado coincide con boards.json y resuelve la ficha."""
    write_template(tmp_path / "templates", "arduino_uno_r4_wifi", make_texture())

    with ServerHarness(tmp_path, frame=_build_frame(), present=True) as harness:
        templates = harness.app.detector._snapshot_templates()
        assert [template.label for template in templates] == ["arduino_uno_r4_wifi"]
        assert "'" not in templates[0].label

        harness.camera.frame_queue.put(_build_frame())
        detection = _wait_for_detection(harness)
        assert detection is not None, "El detector no publicó la placa entrenada"
        assert detection["label"] == "arduino_uno_r4_wifi"
        assert detection["name"] == "Arduino UNO R4 WiFi"
        assert detection["short_name"] == "UNO R4 WiFi"
        assert detection["summary"]


def test_detection_with_label_outside_catalog_degrades(tmp_path: Path) -> None:
    """Un label sin ficha (p. ej. ``raspberry_pi_2``) no rompe la API ni la UI.

    Las plantillas viejas siguen en la placa aunque su ficha ya no esté en el
    catálogo: ``/api/detections`` debe devolver el label crudo, sin ``summary``
    y sin lanzar excepciones (la UI muestra "No hay una ficha técnica…").
    """
    write_template(tmp_path / "templates", "raspberry_pi_2", make_texture())

    with ServerHarness(tmp_path, frame=_build_frame(), present=True) as harness:
        harness.camera.frame_queue.put(_build_frame())
        response = requests.get(f"{harness.base_url}/api/detections", timeout=5)
        assert response.status_code == 200
        detection = _wait_for_detection(harness)
        assert detection is not None, "El detector no publicó la plantilla vieja"
        assert detection["label"] == "raspberry_pi_2"
        assert detection["name"] == "raspberry_pi_2"
        assert detection["short_name"] == "raspberry_pi_2"
        assert detection["summary"] == {}

        boards = requests.get(f"{harness.base_url}/api/boards", timeout=5)
        assert boards.status_code == 200
        assert [board["id"] for board in boards.json()] == [
            "arduino_uno_r4_wifi",
            "raspberry_pi_4",
            "arduino_mkr_wifi_1010",
            "esp32",
        ]
