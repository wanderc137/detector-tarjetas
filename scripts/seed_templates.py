#!/usr/bin/env python3
"""Sembrador de plantillas ORB a partir de imágenes de referencia.

Convierte una o varias imágenes (fotos cenitales de una placa) en plantillas
``.npz`` compatibles con el detector, sin necesidad de capturarlas con la cámara
(modo enseñar). Usa exactamente la misma configuración ORB y el mismo formato de
archivo que ``app.detector.save_template_archive``.

Uso:
    python3 seed_templates.py <label> <plantillas_dir> <img1> [img2 ...]

Ejemplo:
    python3 seed_templates.py arduino_uno_r3 data/templates uno_1.jpg uno_2.jpg

Las plantillas se guardan en ``<plantillas_dir>/<label>/seed-<n>.npz``.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import cv2
import numpy as np

# Mismos parámetros que ``app.config`` / ``app.detector.create_orb``.
ORB_NFEATURES = 500
ORB_EDGE_THRESHOLD = 31
ORB_PATCH_SIZE = 31
MIN_KEYPOINTS = 50


def create_orb() -> cv2.ORB:
    return cv2.ORB_create(
        nfeatures=ORB_NFEATURES,
        edgeThreshold=ORB_EDGE_THRESHOLD,
        patchSize=ORB_PATCH_SIZE,
        fastThreshold=20,
        scoreType=cv2.ORB_HARRIS_SCORE,
    )


def save_template_archive(
    path: Path,
    keypoints,
    descriptors: np.ndarray,
    image_size: tuple[int, int],
    label: str,
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    points = np.float32([kp.pt for kp in keypoints]) if keypoints else np.zeros((0, 2), np.float32)
    sizes = np.float32([kp.size for kp in keypoints]) if keypoints else np.zeros((0,), np.float32)
    angles = np.float32([kp.angle for kp in keypoints]) if keypoints else np.zeros((0,), np.float32)
    responses = np.float32([kp.response for kp in keypoints]) if keypoints else np.zeros((0,), np.float32)
    octaves = np.int32([kp.octave for kp in keypoints]) if keypoints else np.zeros((0,), np.int32)
    class_ids = np.int32([kp.class_id if kp.class_id is not None else -1 for kp in keypoints]) if keypoints else np.zeros((0,), np.int32)

    np.savez_compressed(
        path,
        label=np.array(label),
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


def main() -> int:
    if len(sys.argv) < 4:
        print(__doc__)
        return 2

    label = sys.argv[1]
    templates_dir = Path(sys.argv[2])
    images = [Path(p) for p in sys.argv[3:]]

    orb = create_orb()
    saved = 0
    skipped = 0

    for img_path in images:
        frame = cv2.imread(str(img_path), cv2.IMREAD_COLOR)
        if frame is None:
            print(f"[skip] no se pudo leer {img_path}")
            skipped += 1
            continue

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        keypoints, descriptors = orb.detectAndCompute(gray, None)
        count = 0 if keypoints is None else len(keypoints)

        if descriptors is None or count < MIN_KEYPOINTS:
            print(f"[skip] {img_path.name}: {count}/{MIN_KEYPOINTS} keypoints")
            skipped += 1
            continue

        h, w = gray.shape[:2]
        out = templates_dir / label / f"seed-{int(time.time() * 1000)}-{saved:03d}.npz"
        save_template_archive(out, keypoints, descriptors, (w, h), label)
        print(f"[ok] {img_path.name} -> {out.name} ({count} keypoints, {w}x{h})")
        saved += 1

    print(f"Resumen {label}: {saved} plantillas guardadas, {skipped} omitidas.")
    return 0 if saved else 1


if __name__ == "__main__":
    raise SystemExit(main())
