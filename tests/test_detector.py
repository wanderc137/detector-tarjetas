"""Tests del detector ORB con imágenes sintéticas (CT04–CT07)."""

from __future__ import annotations

import queue
from pathlib import Path

import cv2
import numpy as np
import pytest

from app import config
from app.detector import (
    BoardDetector,
    Template,
    box_iou,
    normalize_label,
    points_in_scan_quad,
    scan_quad_bbox,
    scan_quad_pixels,
)
from tests.helpers import make_scene, make_texture, place_texture, write_template


@pytest.fixture(autouse=True)
def full_frame_scan_quad(monkeypatch: pytest.MonkeyPatch) -> None:
    """Los tests sintéticos usan coordenadas arbitrarias: área = frame completo."""
    monkeypatch.setattr(
        config,
        "SCAN_QUAD",
        ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)),
    )


@pytest.fixture()
def default_scan_quad(
    full_frame_scan_quad: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Restaura el cuadrado calibrado (226×226 px en frame portrait 480×640).

    El fixture autouse ``full_frame_scan_quad`` deja ``SCAN_QUAD`` como el frame
    completo para el resto de los tests sintéticos. Los tests de visibilidad
    necesitan el quad real para ejercitar ``MIN_VISIBLE_QUAD_RATIO`` contra el
    área de escaneo calibrada (I-01); depende de ``full_frame_scan_quad`` para
    que el override se aplique siempre después.
    """
    monkeypatch.setattr(config, "SCAN_QUAD", config.DEFAULT_SCAN_QUAD)


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


def _translation_homography(dx: float, dy: float) -> np.ndarray:
    """Homografía sintética de traslación en píxeles."""
    return np.array([[1.0, 0.0, dx], [0.0, 1.0, dy], [0.0, 0.0, 1.0]], dtype=np.float64)


def _synthetic_template(label: str = "sintetica", width: int = 200, height: int = 150) -> Template:
    """Plantilla mínima para ejercitar ``_build_detection`` sin ORB."""
    points = np.float32(
        [[10, 10], [90, 10], [190, 10], [10, 75], [190, 75], [10, 140], [90, 140], [190, 140]]
    )
    return Template(
        label=label,
        path=Path("sintetica.npz"),
        points=points,
        descriptors=np.zeros((len(points), 32), dtype=np.uint8),
        image_size=(width, height),
        keypoints=[],
    )


def _build_synthetic_detection(
    detector: BoardDetector,
    dx: float,
    dy: float,
    frame_size: tuple[int, int] = (640, 480),
):
    """Proyecta el rectángulo de la plantilla con una traslación conocida."""
    template = _synthetic_template()
    src = template.points
    good = [
        cv2.DMatch(_queryIdx=index, _trainIdx=index, _distance=18.0)
        for index in range(len(src))
    ]
    inlier_mask = np.ones(len(src), dtype=bool)
    return detector._build_detection(
        template=template,
        good=good,
        inlier_mask=inlier_mask,
        src=src,
        homography=_translation_homography(dx, dy),
        frame_size=frame_size,
    )


def test_visibility_filter_rejects_quad_mostly_outside_frame(
    detector: BoardDetector, default_scan_quad: None
) -> None:
    """I-01: un quad con ~69 % dentro del cuadrado calibrado se descarta."""
    # Frame portrait 480×640; cuadrado de escaneo x 84.6..310.9, y 189.6..415.9.
    # La plantilla sintética se proyecta en x 181..369, y 226..364: la
    # intersección es 129.9×138 px (≈69 % del área del quad), no cero. El
    # resto de los filtros (área de caja, relleno, score) sí lo aceptarían, así
    # que si se elimina el filtro de visibilidad este test falla.
    assert (
        _build_synthetic_detection(detector, dx=175.0, dy=220.0, frame_size=(480, 640))
        is None
    )


def test_visibility_filter_accepts_quad_inside_frame(
    detector: BoardDetector, default_scan_quad: None
) -> None:
    """I-01: un quad completamente dentro del cuadrado calibrado se acepta."""
    # x 106..294, y 226..364: 100 % dentro del cuadrado de escaneo.
    detection = _build_synthetic_detection(detector, dx=100.0, dy=220.0, frame_size=(480, 640))

    assert detection is not None
    assert detection.label == "sintetica"
    assert detection.score >= config.SCORE_THRESHOLD


def test_scan_quad_parser_defaults_and_validation() -> None:
    """El cuadrilátero calibrado se usa por defecto y los valores inválidos caen al default."""
    assert config.DEFAULT_SCAN_QUAD == pytest.approx(
        (
            (0.1762, 0.2962),
            (0.6477, 0.2962),
            (0.6477, 0.6499),
            (0.1762, 0.6499),
        )
    )
    assert config._parse_scan_quad(None) == config.DEFAULT_SCAN_QUAD
    assert config._parse_scan_quad("0.1,0.1,0.9,0.1,0.9,0.9,0.1,0.9") == (
        (0.1, 0.1),
        (0.9, 0.1),
        (0.9, 0.9),
        (0.1, 0.9),
    )
    assert config._parse_scan_quad("0,0,1,0,1,1") == config.DEFAULT_SCAN_QUAD
    assert config._parse_scan_quad("0,0,2,0,1,1,0,1") == config.DEFAULT_SCAN_QUAD
    assert config._parse_scan_quad("a,b,c,d,e,f,g,h") == config.DEFAULT_SCAN_QUAD


def test_scan_quad_default_maps_to_t_marks(monkeypatch: pytest.MonkeyPatch) -> None:
    """El área por defecto es un cuadrado centrado en las cuatro T, ampliado 6 %."""
    monkeypatch.setattr(config, "SCAN_QUAD", config.DEFAULT_SCAN_QUAD)
    quad = scan_quad_pixels((480, 640))
    assert quad == pytest.approx(
        np.float32(
            [
                (84.576, 189.568),
                (310.896, 189.568),
                (310.896, 415.936),
                (84.576, 415.936),
            ]
        ),
        abs=0.01,
    )
    assert scan_quad_bbox((480, 640)) == (84, 189, 311, 416)


def test_points_in_scan_quad_uses_polygon(monkeypatch: pytest.MonkeyPatch) -> None:
    """El filtrado de keypoints respeta el cuadrilátero, no su bounding box."""
    monkeypatch.setattr(config, "SCAN_QUAD", config.DEFAULT_SCAN_QUAD)
    points = np.float32(
        [(200, 300), (100, 240), (50, 240), (200, 150), (91, 235), (304, 213)]
    )
    mask = points_in_scan_quad(points, (480, 640))
    assert mask.tolist() == [True, True, False, False, True, True]


def test_template_filtered_to_scan_quad(
    detector: BoardDetector, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Una plantilla con pocos keypoints dentro del quad se descarta entera."""
    monkeypatch.setattr(config, "SCAN_QUAD", config.DEFAULT_SCAN_QUAD)
    points = np.float32(
        [(130, 260), (200, 300), (300, 300), (400, 500), (10, 10), (460, 620)]
    )
    template = Template(
        label="sintetica",
        path=Path("sintetica.npz"),
        points=points,
        descriptors=np.zeros((len(points), 32), dtype=np.uint8),
        image_size=(480, 640),
        keypoints=[],
    )
    filtered_points, filtered_descriptors = detector._filter_template_scan_quad(template)
    assert len(filtered_points) == 3
    assert filtered_descriptors.shape == (3, 32)
    assert (
        detector._match_template(
            template,
            np.zeros((20, 32), dtype=np.uint8),
            np.zeros((20, 2), dtype=np.float32),
            (480, 640),
        )
        == []
    )


def test_visibility_filter_uses_scan_quad_polygon(
    detector: BoardDetector, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El filtro de visibilidad toma el cuadrilátero calibrado como referencia."""
    monkeypatch.setattr(config, "SCAN_QUAD", config.DEFAULT_SCAN_QUAD)
    # El quad proyectado cae solo ~55 % dentro del cuadrilátero (aunque esté
    # dentro del frame): se descarta.
    assert _build_synthetic_detection(detector, dx=0.0, dy=230.0, frame_size=(480, 640)) is None
    # Desplazado al centro del cuadrilátero: se acepta.
    detection = _build_synthetic_detection(
        detector, dx=120.0, dy=230.0, frame_size=(480, 640)
    )
    assert detection is not None
    assert detection.label == "sintetica"


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
