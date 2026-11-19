"""Tests del detector ORB con imágenes sintéticas (CT04–CT07)."""

from __future__ import annotations

import queue
from pathlib import Path

import numpy as np
import pytest

from app import config
from app.detector import BoardDetector, box_iou, normalize_label
from tests.helpers import make_scene, make_texture, place_texture, write_template


@pytest.fixture(scope="module")
def texture() -> np.ndarray:
    """Textura sintética que simula una placa con texto y componentes."""
    return make_texture()


@pytest.fixture()
def detector(tmp_path: Path, texture: np.ndarray) -> BoardDetector:
    """Detector con una plantilla ``arduino_uno_r4_wifi`` escrita en un directorio temporal."""
    root = tmp_path / "templates"
    write_template(root, "arduino_uno_r4_wifi", texture)
    return BoardDetector(frame_queue=queue.Queue(maxsize=2), template_dir=root)


def test_detect_single_instance(detector: BoardDetector, texture: np.ndarray) -> None:
    """CT04: una placa rotada/escalada se detecta con score alto."""
    scene = make_scene()
    place_texture(scene, texture, center=(320, 240), scale=0.82, angle=7.0)

    detections = detector.detect_frame(scene)

    assert detections, "No se detectó ninguna placa en la escena sintética"
    top = detections[0]
    assert top.label == "arduino_uno_r4_wifi"
    assert top.score >= 0.6, f"Score demasiado bajo: {top.score}"
    assert top.instance_id == 0
    assert 0.0 <= top.box[0] <= 1.0
    assert 0.0 <= top.box[1] <= 1.0
    assert 0.0 < top.box[2] <= 1.0
    assert 0.0 < top.box[3] <= 1.0


def test_detect_two_instances(detector: BoardDetector, texture: np.ndarray) -> None:
    """CT05: dos instancias de la misma placa producen dos cajas distintas."""
    scene = make_scene()
    place_texture(scene, texture, center=(170, 235), scale=0.72, angle=-8.0, brightness=0.92)
    place_texture(scene, texture, center=(475, 245), scale=0.78, angle=9.0, brightness=1.05)

    detections = [d for d in detector.detect_frame(scene) if d.label == "arduino_uno_r4_wifi"]

    assert len(detections) >= 2, f"Se esperaban 2 instancias y se detectaron {len(detections)}"
    assert {0, 1}.issubset({d.instance_id for d in detections})
    centers = sorted(d.center[0] for d in detections[:2])
    assert centers[1] - centers[0] > 0.2


def test_blank_image_has_no_detections(detector: BoardDetector) -> None:
    """CT06: un frame sin placas devuelve una lista vacía."""
    assert detector.detect_frame(make_scene()) == []


def test_unrelated_texture_is_rejected(detector: BoardDetector) -> None:
    """CT07: una textura distinta no supera el umbral ni el mínimo de inliers."""
    unrelated = make_texture(seed=99, width=380, height=260)
    scene = make_scene()
    place_texture(scene, unrelated, center=(320, 240), scale=0.9, angle=3.0)

    assert detector.detect_frame(scene) == []


def test_detector_without_templates_returns_empty(tmp_path: Path, texture: np.ndarray) -> None:
    """Sin plantillas no hay detecciones y no se producen errores."""
    detector = BoardDetector(frame_queue=queue.Queue(maxsize=2), template_dir=tmp_path / "vacio")
    scene = make_scene()
    place_texture(scene, texture, center=(320, 240))
    assert detector.detect_frame(scene) == []


def test_reload_templates(detector: BoardDetector, tmp_path: Path, texture: np.ndarray) -> None:
    """``reload_templates`` refleja altas y bajas de archivos en disco."""
    assert detector.template_counts.get("arduino_uno_r4_wifi") == 1

    root = detector.template_dir
    write_template(root, "raspberry_pi_4", texture, name="pi5.npz")
    counts = detector.reload_templates()
    assert counts.get("raspberry_pi_4") == 1

    for path in (root / "raspberry_pi_4").glob("*.npz"):
        path.unlink()
    counts = detector.reload_templates()
    assert "raspberry_pi_4" not in counts


def test_box_iou() -> None:
    """La métrica IoU se comporta como se espera."""
    assert box_iou((0.0, 0.0, 1.0, 1.0), (0.0, 0.0, 1.0, 1.0)) == pytest.approx(1.0)
    assert box_iou((0.0, 0.0, 0.5, 0.5), (0.5, 0.5, 0.5, 0.5)) == 0.0
    assert box_iou((0.0, 0.0, 0.5, 0.5), (0.0, 0.0, 0.5, 0.25)) == pytest.approx(0.5)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (np.array("arduino_uno_r4_wifi"), "arduino_uno_r4_wifi"),  # array 0-d de np.savez
        (np.array("'arduino_uno_r4_wifi'"), "arduino_uno_r4_wifi"),  # archivo viejo con comillas
        (np.array(""), "fallback"),
        (None, "fallback"),
        (b"raspberry_pi_4", "raspberry_pi_4"),
    ],
)
def test_normalize_label(raw: object, expected: str) -> None:
    """I-01: ``normalize_label`` extrae el escalar y elimina comillas."""
    assert normalize_label(raw, "fallback") == expected


def test_template_label_roundtrip_is_clean(tmp_path: Path, texture: np.ndarray) -> None:
    """I-01: guardar y recargar una plantilla deja el label sin comillas."""
    root = tmp_path / "templates"
    write_template(root, "arduino_uno_r4_wifi", texture)

    detector = BoardDetector(frame_queue=queue.Queue(maxsize=2), template_dir=root)

    templates = detector._snapshot_templates()
    assert len(templates) == 1
    assert templates[0].label == "arduino_uno_r4_wifi"
    assert "'" not in templates[0].label
    assert detector.template_counts == {"arduino_uno_r4_wifi": 1}

    scene = make_scene()
    place_texture(scene, texture, center=(320, 240), scale=0.82, angle=6.0)
    detections = detector.detect_frame(scene)
    assert detections, "La plantilla recargada no detecta la placa"
    assert detections[0].label == "arduino_uno_r4_wifi"


def test_score_threshold_is_respected(detector: BoardDetector, texture: np.ndarray) -> None:
    """Toda detección publicada supera el umbral configurado."""
    scene = make_scene()
    place_texture(scene, texture, center=(320, 240), scale=0.8, angle=4.0)
    for detection in detector.detect_frame(scene):
        assert detection.score >= config.SCORE_THRESHOLD
        assert detection.inliers >= config.MIN_INLIERS
