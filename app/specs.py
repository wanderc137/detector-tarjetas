"""Carga y validación de las fichas técnicas de ``data/boards.json``.

El archivo es la fuente de verdad de los datos que muestra la interfaz. Se
valida al cargar para fallar temprano con mensajes claros (y para que el
endpoint ``/api/boards`` devuelva un error 500 explicable si alguien lo rompe).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import config

LOGGER = logging.getLogger(__name__)

REQUIRED_FIELDS: tuple[str, ...] = (
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

_STRING_FIELDS = ("id", "name", "short_name", "mcu_soc", "description")
_NUMBER_FIELDS = ("clock_mhz", "ram_mb", "flash_storage_gb", "voltage_v")


def format_ram(ram_mb: float) -> str:
    """Formatea la RAM para mostrarla en la interfaz.

    Args:
        ram_mb: cantidad de RAM en megabytes.

    Returns:
        Cadena legible, p. ej. ``"2 KB"``, ``"512 MB"`` o ``"4 GB"``.
    """
    if ram_mb >= 1024:
        return f"{ram_mb / 1024:g} GB"
    if ram_mb >= 1:
        return f"{ram_mb:g} MB"
    return f"{max(1.0, ram_mb * 1024):.0f} KB"


def format_storage(flash_storage_gb: float) -> str:
    """Formatea el almacenamiento flash para la interfaz."""
    if flash_storage_gb <= 0:
        return "microSD"
    if flash_storage_gb < 0.001:
        return f"{flash_storage_gb * 1024 * 1024:.0f} KB"
    if flash_storage_gb < 1:
        return f"{flash_storage_gb * 1024:.0f} MB"
    return f"{flash_storage_gb:g} GB"


def format_clock(clock_mhz: float) -> str:
    """Formatea la frecuencia de reloj (MHz o GHz)."""
    if clock_mhz >= 1000:
        return f"{clock_mhz / 1000:g} GHz"
    return f"{clock_mhz:g} MHz"


def format_dimensions(dimensions_mm: tuple[float, ...]) -> str:
    """Formatea las dimensiones en milímetros."""
    return " × ".join(f"{value:g}" for value in dimensions_mm) + " mm"


@dataclass(frozen=True)
class BoardSpec:
    """Ficha técnica completa de una placa."""

    id: str
    name: str
    short_name: str
    mcu_soc: str
    clock_mhz: float
    ram_mb: float
    flash_storage_gb: float
    gpio_count: int
    voltage_v: float
    connectivity: tuple[str, ...]
    dimensions_mm: tuple[float, ...]
    year: int
    description: str
    image_path: Optional[str] = None
    urls: tuple[str, ...] = field(default_factory=tuple)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serializa la ficha completa para la API."""
        payload: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "short_name": self.short_name,
            "mcu_soc": self.mcu_soc,
            "clock_mhz": self.clock_mhz,
            "ram_mb": self.ram_mb,
            "flash_storage_gb": self.flash_storage_gb,
            "gpio_count": self.gpio_count,
            "voltage_v": self.voltage_v,
            "connectivity": list(self.connectivity),
            "dimensions_mm": list(self.dimensions_mm),
            "year": self.year,
            "description": self.description,
            "image_path": self.image_path,
            "urls": list(self.urls),
        }
        for key, value in self.extra.items():
            payload.setdefault(key, value)
        return payload

    def formatted(self) -> dict[str, str]:
        """Valores formateados para mostrar en el panel lateral."""
        return {
            "mcu_soc": self.mcu_soc,
            "clock": format_clock(self.clock_mhz),
            "ram": format_ram(self.ram_mb),
            "storage": format_storage(self.flash_storage_gb),
            "gpio": f"{self.gpio_count}",
            "voltage": f"{self.voltage_v:g} V",
            "dimensions": format_dimensions(self.dimensions_mm),
            "connectivity": ", ".join(self.connectivity),
            "year": str(self.year),
        }


class BoardSpecs:
    """Acceso a las fichas técnicas con caché invalidada por ``mtime``."""

    def __init__(self, path: Optional[Path] = None) -> None:
        """Inicializa el repositorio de fichas.

        Args:
            path: ruta a ``boards.json``; por defecto ``config.BOARDS_JSON``.
        """
        self.path = Path(path) if path is not None else config.BOARDS_JSON
        self._cache: list[BoardSpec] = []
        self._cache_key: Optional[tuple[int, int]] = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    def get_all(self) -> list[BoardSpec]:
        """Devuelve todas las fichas válidas.

        Raises:
            FileNotFoundError: si ``boards.json`` no existe.
            ValueError: si el JSON es inválido o falta un campo obligatorio.
        """
        return list(self._load())

    def get_by_id(self, board_id: str) -> Optional[BoardSpec]:
        """Busca una ficha por ``id``; devuelve ``None`` si no existe."""
        for spec in self._load():
            if spec.id == board_id:
                return spec
        return None

    def get_summary(self, board_id: str) -> dict[str, str]:
        """Devuelve las 3 especificaciones cortas para el globo cómic.

        Args:
            board_id: identificador de la placa.

        Returns:
            Diccionario ordenado con claves ``MCU``, ``RAM`` y ``Reloj``.
            Vacío si la placa no existe.
        """
        spec = self.get_by_id(board_id)
        if spec is None:
            return {}
        mcu_short = spec.mcu_soc.split("(")[0].strip() or spec.mcu_soc
        return {
            "MCU": mcu_short,
            "RAM": format_ram(spec.ram_mb),
            "Reloj": format_clock(spec.clock_mhz),
        }

    def ids(self) -> list[str]:
        """Lista de identificadores disponibles."""
        return [spec.id for spec in self._load()]

    # ------------------------------------------------------------------
    def _load(self) -> list[BoardSpec]:
        """Carga el archivo si cambió desde la última lectura."""
        try:
            stat = os.stat(self.path)
        except FileNotFoundError as exc:
            raise FileNotFoundError(
                f"No se encontró el archivo de fichas técnicas: {self.path}"
            ) from exc

        key = (stat.st_mtime_ns, stat.st_size)
        with self._lock:
            if key == self._cache_key:
                return self._cache

            try:
                with open(self.path, "r", encoding="utf-8") as handle:
                    raw = json.load(handle)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"JSON inválido en {self.path}: línea {exc.lineno}, columna {exc.colno}: {exc.msg}"
                ) from exc

            if not isinstance(raw, list) or not raw:
                raise ValueError(f"{self.path} debe contener una lista no vacía de fichas")

            specs = [self._parse_board(item, index) for index, item in enumerate(raw)]
            seen: set[str] = set()
            for spec in specs:
                if spec.id in seen:
                    raise ValueError(f"Id de placa duplicado en {self.path}: '{spec.id}'")
                seen.add(spec.id)

            self._cache = specs
            self._cache_key = key
            LOGGER.info("Fichas técnicas cargadas: %s", ", ".join(spec.id for spec in specs))
            return specs

    def _parse_board(self, raw: Any, index: int) -> BoardSpec:
        """Valida y convierte una entrada cruda del JSON."""
        if not isinstance(raw, dict):
            raise ValueError(f"La ficha #{index} de {self.path} no es un objeto JSON")

        missing = [field for field in REQUIRED_FIELDS if field not in raw]
        if missing:
            identifier = raw.get("id", f"#{index}")
            raise ValueError(
                f"La ficha '{identifier}' de {self.path} no tiene los campos obligatorios: "
                + ", ".join(missing)
            )

        for field in _STRING_FIELDS:
            if not isinstance(raw[field], str) or not raw[field].strip():
                raise ValueError(
                    f"La ficha '{raw.get('id', index)}' tiene el campo '{field}' vacío o no textual"
                )

        numbers: dict[str, float] = {}
        for field in _NUMBER_FIELDS:
            value = raw[field]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(
                    f"La ficha '{raw['id']}' tiene el campo '{field}' no numérico: {value!r}"
                )
            numbers[field] = float(value)

        gpio_count = raw["gpio_count"]
        if isinstance(gpio_count, bool) or not isinstance(gpio_count, int):
            raise ValueError(f"La ficha '{raw['id']}' tiene 'gpio_count' no entero")
        year = raw["year"]
        if isinstance(year, bool) or not isinstance(year, int):
            raise ValueError(f"La ficha '{raw['id']}' tiene 'year' no entero")

        connectivity = raw["connectivity"]
        if not isinstance(connectivity, list) or not all(isinstance(x, str) for x in connectivity):
            raise ValueError(f"La ficha '{raw['id']}' tiene 'connectivity' inválido")

        dimensions = raw["dimensions_mm"]
        if (
            not isinstance(dimensions, list)
            or len(dimensions) < 2
            or not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in dimensions)
        ):
            raise ValueError(
                f"La ficha '{raw['id']}' tiene 'dimensions_mm' inválido (lista de 2 o más números)"
            )

        urls = raw.get("urls", [])
        if urls is None:
            urls = []
        if not isinstance(urls, list) or not all(isinstance(x, str) for x in urls):
            raise ValueError(f"La ficha '{raw['id']}' tiene 'urls' inválido")
        image_path = raw.get("image_path")
        if image_path is not None and not isinstance(image_path, str):
            raise ValueError(f"La ficha '{raw['id']}' tiene 'image_path' inválido")

        extra = {
            key: value
            for key, value in raw.items()
            if key
            not in {
                *REQUIRED_FIELDS,
                "image_path",
                "urls",
            }
        }

        return BoardSpec(
            id=raw["id"].strip(),
            name=raw["name"].strip(),
            short_name=raw["short_name"].strip(),
            mcu_soc=raw["mcu_soc"].strip(),
            clock_mhz=numbers["clock_mhz"],
            ram_mb=numbers["ram_mb"],
            flash_storage_gb=numbers["flash_storage_gb"],
            gpio_count=int(gpio_count),
            voltage_v=numbers["voltage_v"],
            connectivity=tuple(connectivity),
            dimensions_mm=tuple(float(x) for x in dimensions),
            year=int(year),
            description=raw["description"].strip(),
            image_path=image_path,
            urls=tuple(urls),
            extra=extra,
        )
