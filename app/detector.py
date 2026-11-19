"""Detección y clasificación de placas mediante ORB + homografía.

Estrategia:
    1. Se extraen keypoints/descriptores ORB del frame.
    2. Para cada plantilla aprendida (``data/templates/<label>/*.npz``) se hace
       ``knnMatch`` con distancia Hamming y ratio test de Lowe.
    3. Con los matches buenos se estima una homografía (RANSAC) y se proyecta
       el rectángulo de la plantilla al frame.
    4. Para admitir varias instancias del mismo modelo se repite el proceso
       eliminando los descriptores ya asignados a la instancia encontrada (así
       el ratio test vuelve a ser discriminante para la segunda placa).
    5. Se descartan falsos positivos por número de inliers, área y score, y se
       agrupan/suprimen cajas solapadas con NMS.

La serialización de plantillas usa ``numpy.savez_compressed`` y guarda los
campos de ``cv2.KeyPoint`` necesarios para reconstruirlos.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterable, Optional, Sequence

import cv2
import numpy as np

from . import config

LOGGER = logging.getLogger(__name__)

Box = tuple[float, float, float, float]


# ---------------------------------------------------------------------------
# Utilidades de serialización de plantillas
# ---------------------------------------------------------------------------
def normalize_label(raw: object, fallback: str = "") -> str:
    """Normaliza el label leído/escrito en una plantilla ``.npz``.

    ``numpy.savez`` guarda el label como un array 0-d de Unicode; aplicar
    ``str()`` sobre él devuelve la representación del escalar **entre comillas
    simples** (``"'arduino_uno_r4_wifi'"``). Esta función extrae el escalar con
    ``item()``, decodifica bytes si hace falta y elimina comillas residuales de
    archivos guardados por versiones anteriores.

    Args:
        raw: valor almacenado (array de numpy, bytes, str u otro escalar).
        fallback: valor a devolver si el label queda vacío o ausente.

    Returns:
        El label limpio, sin comillas, o ``fallback``.
    """
    value: object = raw
    if isinstance(value, np.ndarray):
        value = value.item() if value.shape == () else value.tolist()
        if isinstance(value, list):
            value = value[0] if len(value) == 1 else "".join(str(item) for item in value)
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")

    text = str(value).strip() if value is not None else ""
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        text = text[1:-1].strip()
    return text or fallback


def create_orb(
    nfeatures: int = config.ORB_FEATURES,
    edge_threshold: int = config.ORB_EDGE_THRESHOLD,
    patch_size: int = config.ORB_PATCH_SIZE,
) -> cv2.ORB:
    """Crea un extractor ORB con la configuración del proyecto.

    Args:
        nfeatures: cantidad máxima de keypoints.
        edge_threshold: umbral de borde para descartar keypoints.
        patch_size: tamaño del parche descriptivo.

    Returns:
        Instancia configurada de ``cv2.ORB``.
    """
    return cv2.ORB_create(
        nfeatures=nfeatures,
        edgeThreshold=edge_threshold,
        patchSize=patch_size,
        fastThreshold=20,
        scoreType=cv2.ORB_HARRIS_SCORE,
    )


def save_template_archive(
    path: Path,
    keypoints: Sequence[cv2.KeyPoint],
    descriptors: np.ndarray,
    image_size: tuple[int, int],
    label: str,
) -> Path:
    """Guarda una plantilla ORB en formato ``.npz`` comprimido.

    Args:
        path: archivo destino (se crean los directorios padres).
        keypoints: lista de keypoints detectados en la imagen enseñada.
        descriptors: matriz ``(N, 32)`` uint8 de descriptores ORB.
        image_size: tamaño ``(ancho, alto)`` de la imagen de origen.
        label: etiqueta asociada a la muestra.

    Returns:
        La ruta del archivo escrito.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Se guarda siempre un ``str`` limpio (sin comillas ni espacios accidentales)
    # para que el label leído con ``item()`` coincida con los IDs de boards.json.
    clean_label = normalize_label(label)

    points = np.float32([kp.pt for kp in keypoints]) if keypoints else np.zeros((0, 2), np.float32)
    sizes = np.float32([kp.size for kp in keypoints]) if keypoints else np.zeros((0,), np.float32)
    angles = np.float32([kp.angle for kp in keypoints]) if keypoints else np.zeros((0,), np.float32)
    responses = np.float32([kp.response for kp in keypoints]) if keypoints else np.zeros((0,), np.float32)
    octaves = np.int32([kp.octave for kp in keypoints]) if keypoints else np.zeros((0,), np.int32)
    class_ids = np.int32([kp.class_id if kp.class_id is not None else -1 for kp in keypoints]) if keypoints else np.zeros((0,), np.int32)

    np.savez_compressed(
        path,
        label=np.array(clean_label),
        descriptors=np.asarray(descriptors, dtype=np.uint8),
        points=points,
        sizes=sizes,
        angles=angles,
        responses=responses,
        octaves=octaves,
        class_ids=class_ids,
        image_size=np.array(image_size, dtype=np.int32),
    )
    return path


def deserialize_keypoints(data: "np.lib.npyio.NpzFile") -> list[cv2.KeyPoint]:
    """Reconstruye una lista de ``cv2.KeyPoint`` desde un archivo ``.npz``.

    Args:
        data: archivo ``npz`` abierto.

    Returns:
        Lista de keypoints reconstruidos.
    """
    points = data["points"]
    sizes = data["sizes"]
    angles = data["angles"]
    responses = data["responses"]
    octaves = data["octaves"]
    class_ids = data["class_ids"]
    keypoints: list[cv2.KeyPoint] = []
    for i in range(len(points)):
        keypoints.append(
            cv2.KeyPoint(
                float(points[i][0]),
                float(points[i][1]),
                float(sizes[i]),
                float(angles[i]),
                float(responses[i]),
                int(octaves[i]),
                int(class_ids[i]),
            )
        )
    return keypoints


# ---------------------------------------------------------------------------
# Modelos de datos
# ---------------------------------------------------------------------------
@dataclass
class Template:
    """Plantilla ORB cargada en memoria."""

    label: str
    path: Path
    points: np.ndarray
    descriptors: np.ndarray
    image_size: tuple[int, int]
    keypoints: list[cv2.KeyPoint] = field(default_factory=list)


@dataclass
class Detection:
    """Detección de una placa en un frame.

    ``box`` está normalizado a ``[0, 1]`` respecto del tamaño del frame.
    """

    label: str
    score: float
    box: Box
    instance_id: int = 0
    center: tuple[float, float] = (0.5, 0.5)
    inliers: int = 0

    def to_dict(self) -> dict[str, object]:
        """Serializa la detección para la API HTTP."""
        x, y, w, h = self.box
        return {
            "label": self.label,
            "score": round(float(self.score), 3),
            "box": {
                "x": round(float(x), 4),
                "y": round(float(y), 4),
                "w": round(float(w), 4),
                "h": round(float(h), 4),
            },
            "instance_id": int(self.instance_id),
            "inliers": int(self.inliers),
        }


# ---------------------------------------------------------------------------
# Utilidades geométricas
# ---------------------------------------------------------------------------
def box_iou(a: Box, b: Box) -> float:
    """Calcula la intersección sobre unión de dos cajas normalizadas."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ax2, ay2 = ax + aw, ay + ah
    bx2, by2 = bx + bw, by + bh

    inter_w = max(0.0, min(ax2, bx2) - max(ax, bx))
    inter_h = max(0.0, min(ay2, by2) - max(ay, by))
    inter = inter_w * inter_h
    if inter <= 0.0:
        return 0.0
    union = aw * ah + bw * bh - inter
    if union <= 0.0:
        return 0.0
    return inter / union


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------
class BoardDetector(threading.Thread):
    """Hilo de inferencia que publica detecciones a partir de una cola de frames."""

    def __init__(
        self,
        frame_queue: "queue.Queue[np.ndarray]",
        template_dir: Optional[Path] = None,
        interval_ms: int = config.DETECTOR_INTERVAL_MS,
        matcher: Optional[cv2.BFMatcher] = None,
    ) -> None:
        """Inicializa el detector.

        Args:
            frame_queue: cola de la que se leen frames (producida por la cámara).
            template_dir: directorio raíz de plantillas; por defecto
                ``config.TEMPLATE_DIR``.
            interval_ms: tiempo mínimo entre inferencias consecutivas.
            matcher: matcher opcional (útil para tests); por defecto
                ``BFMatcher(NORM_HAMMING)``.
        """
        super().__init__(name="board-detector", daemon=True)
        self.frame_queue = frame_queue
        self.template_dir = Path(template_dir) if template_dir is not None else config.TEMPLATE_DIR
        self.interval_s = max(0.0, interval_ms / 1000.0)

        self._orb = create_orb()
        self._matcher = matcher or cv2.BFMatcher(cv2.NORM_HAMMING)

        self._templates: dict[str, list[Template]] = {}
        self._templates_lock = threading.Lock()
        self._detections: list[Detection] = []
        self._detections_lock = threading.Lock()
        self._last_inference_ms: float = 0.0
        self._inference_count: int = 0
        self._stop_event = threading.Event()

        self.reload_templates()

    # ------------------------------------------------------------------
    # Propiedades
    # ------------------------------------------------------------------
    @property
    def last_inference_ms(self) -> float:
        """Duración de la última inferencia en milisegundos."""
        with self._detections_lock:
            return round(self._last_inference_ms, 1)

    @property
    def inference_count(self) -> int:
        """Cantidad de inferencias ejecutadas desde el arranque."""
        with self._detections_lock:
            return self._inference_count

    @property
    def template_labels(self) -> list[str]:
        """Etiquetas con al menos una plantilla cargada."""
        with self._templates_lock:
            return sorted(label for label, tpls in self._templates.items() if tpls)

    @property
    def template_counts(self) -> dict[str, int]:
        """Cantidad de plantillas cargadas por etiqueta."""
        with self._templates_lock:
            return {label: len(tpls) for label, tpls in sorted(self._templates.items()) if tpls}

    # ------------------------------------------------------------------
    # Plantillas
    # ------------------------------------------------------------------
    def reload_templates(self) -> dict[str, int]:
        """Recarga todas las plantillas desde disco (recarga en caliente).

        Returns:
            Diccionario ``{label: cantidad_de_plantillas}`` cargado.
        """
        loaded: dict[str, list[Template]] = {}
        root = self.template_dir
        if not root.exists():
            LOGGER.info("No existe el directorio de plantillas %s", root)
        else:
            for label_dir in sorted(p for p in root.iterdir() if p.is_dir()):
                label = label_dir.name
                for path in sorted(label_dir.glob(f"*{config.TEMPLATE_EXTENSION}")):
                    try:
                        template = self._load_template(path, label)
                    except (OSError, ValueError, KeyError) as exc:
                        LOGGER.warning("Plantilla inválida %s: %s", path, exc)
                        continue
                    loaded.setdefault(label, []).append(template)

        with self._templates_lock:
            self._templates = loaded

        counts = {label: len(tpls) for label, tpls in loaded.items()}
        LOGGER.info("Plantillas recargadas: %s", counts or "ninguna")
        return counts

    def _load_template(self, path: Path, label: str) -> Template:
        """Carga una plantilla ``.npz`` y valida su contenido."""
        with np.load(path, allow_pickle=False) as data:
            descriptors = np.asarray(data["descriptors"], dtype=np.uint8)
            points = np.asarray(data["points"], dtype=np.float32)
            image_size_raw = np.asarray(data["image_size"], dtype=np.int32).tolist()
            # ``data["label"]`` es un array 0-d de Unicode: hay que usar
            # ``item()`` para no obtener el string con comillas simples. Si el
            # campo falta o queda vacío se usa el nombre del directorio.
            stored_label = normalize_label(data["label"], label) if "label" in data else label
            keypoints = deserialize_keypoints(data)

        if descriptors.ndim != 2 or descriptors.shape[1] != 32:
            raise ValueError("descriptores con forma inesperada")
        if len(points) != len(descriptors):
            raise ValueError("keypoints y descriptores no coinciden")
        if len(image_size_raw) != 2:
            raise ValueError("image_size inválido")

        return Template(
            label=stored_label or label,
            path=path,
            points=points,
            descriptors=descriptors,
            image_size=(int(image_size_raw[0]), int(image_size_raw[1])),
            keypoints=keypoints,
        )

    def _snapshot_templates(self) -> list[Template]:
        """Copia la lista plana de plantillas para trabajar sin lock."""
        with self._templates_lock:
            return [tpl for tpls in self._templates.values() for tpl in tpls]

    # ------------------------------------------------------------------
    # Ciclo del hilo
    # ------------------------------------------------------------------
    def stop(self) -> None:
        """Detiene el hilo de inferencia y espera su finalización."""
        self._stop_event.set()
        if self.is_alive():
            self.join(timeout=2.0)

    def run(self) -> None:  # noqa: D401 - docstring heredado de Thread
        """Bucle de inferencia: lee frames, detecta y publica resultados."""
        LOGGER.info("Hilo de detección iniciado (plantillas=%s)", self.template_counts)
        while not self._stop_event.is_set():
            try:
                frame = self.frame_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if frame is None:
                continue

            started = time.perf_counter()
            try:
                detections = self.detect_frame(frame)
            except cv2.error as exc:  # pragma: no cover - defensivo
                LOGGER.warning("Error de OpenCV durante la detección: %s", exc)
                detections = []

            elapsed_ms = (time.perf_counter() - started) * 1000.0
            with self._detections_lock:
                self._detections = detections
                self._last_inference_ms = elapsed_ms
                self._inference_count += 1

            remaining = self.interval_s - (time.perf_counter() - started)
            if remaining > 0:
                self._stop_event.wait(remaining)

        LOGGER.info("Hilo de detección detenido")

    # ------------------------------------------------------------------
    # Inferencia
    # ------------------------------------------------------------------
    def get_detections(self) -> list[Detection]:
        """Devuelve una copia de las últimas detecciones."""
        with self._detections_lock:
            return list(self._detections)

    def detect_frame(self, frame: np.ndarray) -> list[Detection]:
        """Detecta placas en un frame BGR.

        Args:
            frame: imagen BGR (o escala de grises) de ``HxW`` o ``HxWx3``.

        Returns:
            Lista de detecciones ordenadas por score descendente.
        """
        templates = self._snapshot_templates()
        if not templates or frame is None:
            return []

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        height, width = gray.shape[:2]
        if height < 32 or width < 32:
            return []

        keypoints, descriptors = self._orb.detectAndCompute(gray, None)
        if descriptors is None or len(descriptors) < config.MIN_MATCH_COUNT:
            return []

        frame_points = np.float32([kp.pt for kp in keypoints])
        candidates: list[Detection] = []
        for template in templates:
            candidates.extend(self._match_template(template, descriptors, frame_points, (width, height)))

        return self._postprocess(candidates, (width, height))

    def _match_template(
        self,
        template: Template,
        frame_descriptors: np.ndarray,
        frame_points: np.ndarray,
        frame_size: tuple[int, int],
    ) -> list[Detection]:
        """Busca una plantilla en el frame, admitiendo varias instancias."""
        if len(template.descriptors) < config.MIN_MATCH_COUNT:
            return []

        results: list[Detection] = []
        available = list(range(len(frame_descriptors)))

        for _ in range(config.MAX_INSTANCES_PER_TEMPLATE):
            if len(available) < config.MIN_MATCH_COUNT:
                break

            index_map = np.asarray(available, dtype=np.int32)
            subset = frame_descriptors[index_map]
            try:
                knn = self._matcher.knnMatch(template.descriptors, subset, k=2)
            except cv2.error:  # pragma: no cover - defensivo
                break

            good = [
                pair[0]
                for pair in knn
                if len(pair) == 2 and pair[0].distance < config.LOWE_RATIO * pair[1].distance
            ]
            if len(good) < config.MIN_MATCH_COUNT:
                break

            src = template.points[[m.queryIdx for m in good]].astype(np.float32)
            dst = frame_points[index_map[[m.trainIdx for m in good]]].astype(np.float32)
            if len(src) < 4:
                break

            try:
                homography, mask = cv2.findHomography(
                    src, dst, cv2.RANSAC, config.RANSAC_REPROJ_THRESHOLD
                )
            except cv2.error:  # pragma: no cover - defensivo
                break
            if homography is None or mask is None:
                break

            inlier_mask = mask.ravel().astype(bool)
            inlier_count = int(inlier_mask.sum())
            if inlier_count < config.MIN_INLIERS:
                break

            detection = self._build_detection(
                template=template,
                good=good,
                inlier_mask=inlier_mask,
                src=src,
                homography=homography,
                frame_size=frame_size,
            )
            if detection is not None:
                results.append(detection)
                # Se descartan todos los keypoints del frame que caen dentro de
                # la caja detectada (no solo los inliers): así el ratio test
                # vuelve a ser discriminante para una segunda instancia idéntica.
                width, height = frame_size
                box_x, box_y, box_w, box_h = detection.box
                margin = 8.0
                x_min = box_x * width - margin
                y_min = box_y * height - margin
                x_max = (box_x + box_w) * width + margin
                y_max = (box_y + box_h) * height + margin
                consumed = {
                    idx
                    for idx in available
                    if x_min <= frame_points[idx][0] <= x_max
                    and y_min <= frame_points[idx][1] <= y_max
                }
            else:
                consumed = {
                    int(index_map[good[i].trainIdx]) for i in range(len(good)) if inlier_mask[i]
                }

            if not consumed:
                break
            available = [idx for idx in available if idx not in consumed]

        return results

    def _build_detection(
        self,
        template: Template,
        good: Sequence[cv2.DMatch],
        inlier_mask: np.ndarray,
        src: np.ndarray,
        homography: np.ndarray,
        frame_size: tuple[int, int],
    ) -> Optional[Detection]:
        """Construye y valida una detección a partir de una homografía."""
        width, height = frame_size
        inliers = src[inlier_mask]
        if len(inliers) < 4:
            return None

        x0, y0 = inliers.min(axis=0)
        x1, y1 = inliers.max(axis=0)
        pad = max(4.0, 0.02 * max(x1 - x0, y1 - y0))
        x0, y0 = max(0.0, x0 - pad), max(0.0, y0 - pad)
        x1 = min(float(template.image_size[0]), x1 + pad)
        y1 = min(float(template.image_size[1]), y1 + pad)

        corners = np.float32([[x0, y0], [x1, y0], [x1, y1], [x0, y1]]).reshape(1, -1, 2)
        try:
            projected = cv2.perspectiveTransform(corners, homography).reshape(-1, 2)
        except cv2.error:  # pragma: no cover - defensivo
            return None
        if not np.all(np.isfinite(projected)):
            return None

        polygon_area = abs(float(cv2.contourArea(projected.astype(np.float32))))
        bx, by, bw, bh = cv2.boundingRect(projected.astype(np.float32))
        if bw <= 1 or bh <= 1:
            return None
        if polygon_area < config.MIN_POLYGON_FILL_RATIO * bw * bh:
            return None

        x_start, y_start = max(0, bx), max(0, by)
        x_end, y_end = min(width, bx + bw), min(height, by + bh)
        if x_end - x_start < 8 or y_end - y_start < 8:
            return None

        area_ratio = (x_end - x_start) * (y_end - y_start) / float(width * height)
        if area_ratio < config.MIN_BOX_AREA_RATIO or area_ratio > config.MAX_BOX_AREA_RATIO:
            return None

        inlier_count = int(inlier_mask.sum())
        inlier_ratio = inlier_count / max(1, len(good))
        distances = np.float32([good[i].distance for i in range(len(good)) if inlier_mask[i]])
        mean_distance = float(distances.mean()) if len(distances) else 100.0
        distance_score = max(0.0, min(1.0, 1.0 - mean_distance / 90.0))
        area_score = min(1.0, (area_ratio / config.MIN_BOX_AREA_RATIO) * 0.5)
        score = 0.6 * inlier_ratio + 0.3 * distance_score + 0.1 * area_score

        if score < config.SCORE_THRESHOLD:
            return None

        box: Box = (
            x_start / width,
            y_start / height,
            (x_end - x_start) / width,
            (y_end - y_start) / height,
        )
        return Detection(
            label=template.label,
            score=float(score),
            box=box,
            center=((x_start + x_end) / 2.0 / width, (y_start + y_end) / 2.0 / height),
            inliers=inlier_count,
        )

    def _postprocess(
        self, candidates: Iterable[Detection], frame_size: tuple[int, int]
    ) -> list[Detection]:
        """Aplica NMS, agrupa instancias cercanas y asigna ``instance_id``."""
        width, height = frame_size
        ordered = sorted(candidates, key=lambda d: d.score, reverse=True)

        kept: list[Detection] = []
        for detection in ordered:
            redundant = False
            cx, cy = detection.center
            for other in kept:
                if box_iou(detection.box, other.box) > config.NMS_IOU_THRESHOLD:
                    redundant = True
                    break
                # Contención: si el centro de esta caja cae dentro de otra caja
                # del mismo label, es una instancia fantasma superpuesta.
                if detection.label == other.label:
                    ox, oy, ow, oh = other.box
                    if ox <= cx <= ox + ow and oy <= cy <= oy + oh:
                        redundant = True
                        break
            if not redundant:
                kept.append(detection)

        merged: list[Detection] = []
        for detection in kept:
            for index, existing in enumerate(merged):
                if existing.label != detection.label:
                    continue
                dx = (existing.center[0] - detection.center[0]) * width
                dy = (existing.center[1] - detection.center[1]) * height
                if (dx * dx + dy * dy) ** 0.5 < config.INSTANCE_CENTER_DISTANCE_PX:
                    if detection.score > existing.score:
                        merged[index] = detection
                    break
            else:
                merged.append(detection)

        grouped: dict[str, list[Detection]] = {}
        for detection in merged:
            grouped.setdefault(detection.label, []).append(detection)

        output: list[Detection] = []
        for label, items in grouped.items():
            items.sort(key=lambda d: (d.center[0], d.center[1]))
            for instance_id, detection in enumerate(items):
                output.append(replace(detection, instance_id=instance_id))

        output.sort(key=lambda d: d.score, reverse=True)
        return output
