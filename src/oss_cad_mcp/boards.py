"""FPGA board presets for software build flows."""

from __future__ import annotations

import tomllib
from importlib import resources

from oss_cad_mcp.models.results import BoardInfo


def _load() -> dict[str, BoardInfo]:
    text = resources.files("oss_cad_mcp.data").joinpath("boards.toml").read_text(encoding="utf-8")
    raw = tomllib.loads(text)
    boards: dict[str, BoardInfo] = {}
    for key, value in raw.get("boards", {}).items():
        boards[key] = BoardInfo(id=key, **value)
    return boards


_BOARDS: dict[str, BoardInfo] | None = None


def boards() -> dict[str, BoardInfo]:
    global _BOARDS
    if _BOARDS is None:
        _BOARDS = _load()
    return _BOARDS


def get_board(board_id: str) -> BoardInfo | None:
    return boards().get(board_id)


def list_boards() -> list[BoardInfo]:
    return list(boards().values())
