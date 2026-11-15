#!/usr/bin/env python3
"""Diagnóstico: compara el frame actual contra una plantilla y reporta dónde
caen los inliers del match (para distinguir match real de fondo/espurio)."""
import glob
import sys

import cv2
import numpy as np

FRAME = "/tmp/shot.jpg"


def main():
    tpl_path = sys.argv[1] if len(sys.argv) > 1 else None
    if tpl_path is None:
        files = sorted(glob.glob(
            "/home/jma/detector-tarjetas/data/templates/raspberry_pi_2/*.npz"
        ))
        tpl_path = files[0] if files else None
    if tpl_path is None:
        print("NO_TEMPLATE")
        return

    img = cv2.imread(FRAME)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape

    with np.load(tpl_path, allow_pickle=False) as d:
        tpl_desc = np.asarray(d["descriptors"], dtype=np.uint8)
        tpl_pts = np.asarray(d["points"], dtype=np.float32)
        tpl_size = np.asarray(d["image_size"], dtype=np.int32).tolist()

    orb = cv2.ORB_create(nfeatures=500, edgeThreshold=31, patchSize=31,
                         fastThreshold=20, scoreType=cv2.ORB_HARRIS_SCORE)
    kp, desc = orb.detectAndCompute(gray, None)
    fp = np.float32([k.pt for k in kp])

    print("frame", w, "x", h, "| keypoints frame", len(kp))
    print("template image_size", tpl_size, "| desc", tpl_desc.shape)
    print("tpl pts bbox", tpl_pts[:, 0].min(), tpl_pts[:, 0].max(),
          tpl_pts[:, 1].min(), tpl_pts[:, 1].max())

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    knn = matcher.knnMatch(tpl_desc, desc, k=2)
    good = [p[0] for p in knn if len(p) == 2 and p[0].distance < 0.75 * p[1].distance]
    print("good matches", len(good))

    if len(good) < 10:
        print("pocos matches -> no hay deteccion")
        return

    src = tpl_pts[[m.queryIdx for m in good]].astype(np.float32)
    dst = fp[[m.trainIdx for m in good]].astype(np.float32)
    H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
    if H is None:
        print("sin homografia")
        return
    inl = mask.ravel().astype(bool)
    print("inliers", int(inl.sum()), "/", len(good))

    inl_dst = dst[inl]
    print("inliers en FRAME: x", round(inl_dst[:, 0].min(), 1),
          round(inl_dst[:, 0].max(), 1), "y", round(inl_dst[:, 1].min(), 1),
          round(inl_dst[:, 1].max(), 1))
    # distribucion de inliers en el frame (grid 4x4)
    hist, _, _ = np.histogram2d(inl_dst[:, 0], inl_dst[:, 1], bins=4,
                                range=[[0, w], [0, h]])
    print("inliers grid 4x4 (col x fila):")
    for r in range(4):
        print([int(hist[c][r]) for c in range(4)])


if __name__ == "__main__":
    main()
