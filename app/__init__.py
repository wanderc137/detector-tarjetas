"""Detector de tarjetas electrónicas para Arduino UNO Q.

Aplicación auto-contenida que captura video de una webcam USB, detecta placas
electrónicas mediante ORB + RANSAC y las expone por HTTP (stream MJPEG, API
JSON y páginas web en español).

Módulos:
    config: constantes compartidas y rutas del proyecto.
    camera: hilo de captura V4L2 con reconexión automática.
    detector: hilo de inferencia ORB / homografía / clustering.
    specs: carga y validación de las fichas técnicas (``data/boards.json``).
    teach: captura y borrado de muestras ORB desde el navegador.
    server: servidor HTTP y punto de entrada.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "1.0.0"
