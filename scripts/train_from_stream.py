#!/usr/bin/env python3
"""Genera plantillas ORB a partir de un stream de video (cámara IP).

Graba frames durante ``duracion_seg`` segundos, extrae keypoints ORB de cada
frame y guarda una plantilla sólo si difiere de la anterior (deduplicación por
matcheo de descriptores consecutivos). Así se obtienen plantillas *diversas*
(~100-150) en lugar de miles de frames casi idénticos.

Uso:
    python3 train_from_stream.py <label> <duracion_seg> [url] [out_dir]

Ejemplo (1 minuto, placa Raspberry Pi 2):
    python3 train_from_stream.py raspberry_pi_2 60

Por defecto la cámara es http://192.168.1.7:8080/video (app "IP Webcam").
"""
import os
import sys
import time

import cv2
import numpy as np

# Mismos parámetros que app/detector.py:create_orb()
ORB_KW = dict(
    nfeatures=500,
    edgeThreshold=31,
    patchSize=31,
    fastThreshold=20,
    scoreType=cv2.ORB_HARRIS_SCORE,
)

MIN_KEYPOINTS = 50          # descarta frames sin textura suficiente
DEDUP_GOOD_MATCHES = 40     # si supera esto, el frame es "casi igual" al previo
FRAME_INTERVAL = 0.35       # segundos entre frames evaluados
MAX_TEMPLATES = 150         # tope duro de plantillas por corrida


def make_orb() -> cv2.ORB:
    return cv2.ORB_create(**ORB_KW)


def save_template(path, label, kps, desc, w, h):
    np.savez_compressed(
        path,
        label=np.array(label),
        descriptors=np.asarray(desc, dtype=np.uint8),
        points=np.float32([kp.pt for kp in kps]),
        sizes=np.float32([kp.size for kp in kps]),
        angles=np.float32([kp.angle for kp in kps]),
        responses=np.float32([kp.response for kp in kps]),
        octaves=np.int32([kp.octave for kp in kps]),
        class_ids=np.int32([kp.class_id if kp.class_id is not None else -1 for kp in kps]),
        image_size=np.array([w, h], dtype=np.int32),
    )


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    label = sys.argv[1]
    duration = int(sys.argv[2])
    url = sys.argv[3] if len(sys.argv) > 3 else "http://192.168.1.7:8080/video"
    out_dir = sys.argv[4] if len(sys.argv) > 4 else f"/home/arduino/video_out/{label}"

    os.makedirs(out_dir, exist_ok=True)

    detector = make_orb()
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    cap = cv2.VideoCapture(url)
    if not cap.isOpened():
        print(f"ERROR: no se pudo abrir el stream {url}", flush=True)
        sys.exit(1)

    prev_desc = None
    saved = 0
    t0 = time.time()
    last_grab = 0.0

    print(f"Grabando {duration}s desde {url} -> {out_dir} ...", flush=True)
    while time.time() - t0 < duration and saved < MAX_TEMPLATES:
        ok, frame = cap.read()
        if not ok:
            time.sleep(0.1)
            continue
        now = time.time()
        if now - last_grab < FRAME_INTERVAL:
            continue
        last_grab = now

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        kps, desc = detector.detectAndCompute(gray, None)
        if kps is None or desc is None or len(kps) < MIN_KEYPOINTS:
            continue

        # Deduplicación: descarta el frame si es casi idéntico al anterior.
        if prev_desc is not None:
            matches = matcher.knnMatch(desc, prev_desc, k=2)
            good = [m for m, n in matches if m.distance < 0.75 * n.distance]
            if len(good) > DEDUP_GOOD_MATCHES:
                continue

        path = os.path.join(out_dir, f"video-{int(time.time() * 1000)}-{saved:03d}.npz")
        save_template(path, label, kps, desc, frame.shape[1], frame.shape[0])
        prev_desc = desc
        saved += 1
        print(f"  + plantilla {saved}", flush=True)

    cap.release()
    print(f"TOTAL: {saved} plantillas guardadas en {out_dir}", flush=True)


if __name__ == "__main__":
    main()
