"""Tests de carga y validación de ``data/boards.json`` (CT01–CT03)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.specs import REQUIRED_FIELDS, BoardSpecs, format_clock, format_ram, format_storage

PROJECT_DIR = Path(__file__).resolve().parents[1]
BOARDS_JSON = PROJECT_DIR / "data" / "boards.json"

EXPECTED_IDS = {"arduino_uno_r3", "arduino_nano", "raspberry_pi_4", "raspberry_pi_5"}


@pytest.fixture(scope="module")
def specs() -> BoardSpecs:
    """Repositorio apuntando al ``boards.json`` real del proyecto."""
    return BoardSpecs(BOARDS_JSON)


def test_loads_four_boards_with_required_fields(specs: BoardSpecs) -> None:
    """CT01: el archivo real carga 4 placas y todos los campos obligatorios."""
    boards = specs.get_all()
    assert len(boards) == 4
    assert {board.id for board in boards} == EXPECTED_IDS

    for board in boards:
        payload = board.to_dict()
        for field in REQUIRED_FIELDS:
            assert field in payload, f"Falta {field} en {board.id}"
            assert payload[field] is not None
        assert payload["description"]
        assert payload["dimensions_mm"]
        assert payload["connectivity"]


def test_missing_field_raises_clear_error(tmp_path: Path) -> None:
    """CT02: un campo faltante lanza ``ValueError`` mencionándolo."""
    raw = json.loads(BOARDS_JSON.read_text(encoding="utf-8"))
    del raw[0]["gpio_count"]
    path = tmp_path / "boards.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="gpio_count"):
        BoardSpecs(path).get_all()


def test_invalid_json_raises(tmp_path: Path) -> None:
    """Un JSON malformado produce un error explicable."""
    path = tmp_path / "boards.json"
    path.write_text("{ esto no es json", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON inválido"):
        BoardSpecs(path).get_all()


def test_duplicate_ids_raise(tmp_path: Path) -> None:
    """Los ids duplicados se rechazan para evitar ambigüedad en la API."""
    raw = json.loads(BOARDS_JSON.read_text(encoding="utf-8"))
    raw[1]["id"] = raw[0]["id"]
    path = tmp_path / "boards.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicado"):
        BoardSpecs(path).get_all()


def test_missing_file_raises(tmp_path: Path) -> None:
    """Un archivo inexistente produce ``FileNotFoundError``."""
    with pytest.raises(FileNotFoundError):
        BoardSpecs(tmp_path / "no-existe.json").get_all()


def test_summary_has_three_specs(specs: BoardSpecs) -> None:
    """El globo cómic usa exactamente 3 especificaciones cortas."""
    summary = specs.get_summary("arduino_uno_r3")
    assert list(summary.keys()) == ["MCU", "RAM", "Reloj"]
    assert len(summary) == 3
    assert summary["MCU"] == "ATmega328P"
    assert summary["RAM"] == "2 KB"
    assert specs.get_summary("no_existe") == {}


def test_unknown_board_returns_none(specs: BoardSpecs) -> None:
    """``get_by_id`` devuelve ``None`` para ids desconocidos."""
    assert specs.get_by_id("no_existe") is None


def test_formatting_helpers() -> None:
    """Los formateadores muestran unidades legibles en español."""
    assert format_ram(0.002) == "2 KB"
    assert format_ram(2048) == "2 GB"
    assert format_storage(0) == "microSD"
    assert format_storage(32768 / (1024 * 1024 * 1024)) == "32 KB"
    assert format_clock(16) == "16 MHz"
    assert format_clock(2400) == "2.4 GHz"
