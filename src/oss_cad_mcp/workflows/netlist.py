"""Yosys JSON netlist inspection."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from oss_cad_mcp.app import AppContext
from oss_cad_mcp.errors import InvalidInput
from oss_cad_mcp.models.results import ModuleInfo, NetlistMatch, NetlistReport

NetlistAction = Literal["stats", "modules", "cells", "nets", "fanout", "diff"]


def _load(path: Path) -> dict[str, Any]:
    try:
        import orjson

        data = orjson.loads(path.read_bytes())
    except Exception:
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError) as exc:
            raise InvalidInput(f"Could not parse Yosys JSON netlist: {exc}") from exc
    if not isinstance(data, dict):
        raise InvalidInput("Yosys netlist must be a JSON object.")
    return data


def _modules(data: dict[str, Any]) -> dict[str, Any]:
    modules = data.get("modules", {})
    return modules if isinstance(modules, dict) else {}


def _clean_name(name: str) -> str:
    return name[1:] if name.startswith("\\") else name


def _cell_counts(modules: dict[str, Any]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for module in modules.values():
        if not isinstance(module, dict):
            continue
        cells = module.get("cells", {})
        if isinstance(cells, dict):
            for cell in cells.values():
                if isinstance(cell, dict) and cell.get("type"):
                    counts[str(cell["type"])] += 1
    return dict(sorted(counts.items()))


def _module_info(name: str, module: dict[str, Any]) -> ModuleInfo:
    ports = module.get("ports", {})
    cells = module.get("cells", {})
    netnames = module.get("netnames", {})
    memories = module.get("memories", {})
    return ModuleInfo(
        name=_clean_name(name),
        ports=len(ports) if isinstance(ports, dict) else 0,
        cells=len(cells) if isinstance(cells, dict) else 0,
        wires=len(netnames) if isinstance(netnames, dict) else 0,
        memories=len(memories) if isinstance(memories, dict) else 0,
    )


def _matches_cells(
    modules: dict[str, Any], module_filter: str | None, type_filter: str | None, name_filter: str | None, limit: int
) -> list[NetlistMatch]:
    matches: list[NetlistMatch] = []
    for module_name, module in modules.items():
        if module_filter and module_filter not in {module_name, _clean_name(module_name)}:
            continue
        if not isinstance(module, dict):
            continue
        cells = module.get("cells", {})
        if not isinstance(cells, dict):
            continue
        for cell_name, cell in cells.items():
            if not isinstance(cell, dict):
                continue
            cell_type = str(cell.get("type", ""))
            if type_filter and type_filter.lower() not in cell_type.lower():
                continue
            if name_filter and name_filter.lower() not in str(cell_name).lower():
                continue
            matches.append(
                NetlistMatch(
                    module=_clean_name(str(module_name)),
                    name=_clean_name(str(cell_name)),
                    type=cell_type or None,
                    details={
                        "parameters": cell.get("parameters", {}),
                        "connections": {
                            str(key): str(value) for key, value in (cell.get("connections", {}) or {}).items()
                        },
                    },
                )
            )
            if len(matches) >= limit:
                return matches
    return matches


def _matches_nets(
    modules: dict[str, Any], module_filter: str | None, name_filter: str | None, limit: int
) -> list[NetlistMatch]:
    matches: list[NetlistMatch] = []
    for module_name, module in modules.items():
        if module_filter and module_filter not in {module_name, _clean_name(module_name)}:
            continue
        if not isinstance(module, dict):
            continue
        netnames = module.get("netnames", {})
        if not isinstance(netnames, dict):
            continue
        for net_name, net in netnames.items():
            if name_filter and name_filter.lower() not in str(net_name).lower():
                continue
            details = net if isinstance(net, dict) else {}
            matches.append(
                NetlistMatch(
                    module=_clean_name(str(module_name)),
                    name=_clean_name(str(net_name)),
                    details={
                        "bits": details.get("bits", []),
                        "attributes": details.get("attributes", {}),
                    },
                )
            )
            if len(matches) >= limit:
                return matches
    return matches


def _fanout(modules: dict[str, Any], net_filter: str, limit: int) -> list[NetlistMatch]:
    matches: list[NetlistMatch] = []
    for module_name, module in modules.items():
        if not isinstance(module, dict):
            continue
        netnames = module.get("netnames", {})
        if not isinstance(netnames, dict):
            continue
        selected_bits: set[int] = set()
        selected_names: list[str] = []
        for net_name, net in netnames.items():
            if net_filter.lower() not in str(net_name).lower():
                continue
            selected_names.append(_clean_name(str(net_name)))
            if isinstance(net, dict):
                for bit in net.get("bits", []) or []:
                    if isinstance(bit, int):
                        selected_bits.add(bit)
        loads: list[NetlistMatch] = []
        cells = module.get("cells", {})
        if isinstance(cells, dict):
            for cell_name, cell in cells.items():
                if not isinstance(cell, dict):
                    continue
                for port, value in (cell.get("connections", {}) or {}).items():
                    values = value if isinstance(value, list) else [value]
                    if any(isinstance(bit, int) and bit in selected_bits for bit in values):
                        loads.append(
                            NetlistMatch(
                                module=_clean_name(str(module_name)),
                                name=_clean_name(str(cell_name)),
                                type=str(cell.get("type", "")) or None,
                                details={"port": str(port)},
                            )
                        )
                        if len(loads) >= limit:
                            break
        matches.append(
            NetlistMatch(
                module=_clean_name(str(module_name)),
                name=", ".join(selected_names) or net_filter,
                details={"bits": sorted(selected_bits), "loads": [load.model_dump() for load in loads]},
            )
        )
        if len(matches) >= limit:
            break
    return matches


def inspect_netlist(
    app: AppContext,
    *,
    action: NetlistAction,
    path: str | Path,
    module: str | None = None,
    cell_type: str | None = None,
    name: str | None = None,
    other: str | Path | None = None,
    limit: int = 200,
) -> NetlistReport:
    resolved = app.paths.resolve_read(path, must_exist=True)
    data = _load(resolved)
    modules = _modules(data)
    counts = _cell_counts(modules)
    base = NetlistReport(
        ok=True,
        action=action,
        path=app.paths.display(resolved),
        module_count=len(modules),
        cells_by_type=counts,
        summary=f"{len(modules)} module(s), {sum(counts.values())} cell(s).",
    )
    if action == "modules":
        base.modules = [_module_info(name, module) for name, module in modules.items() if isinstance(module, dict)][
            :limit
        ]
        return base
    if action == "cells":
        base.matches = _matches_cells(modules, module, cell_type, name, limit)
        base.summary = f"Found {len(base.matches)} cell(s)."
        return base
    if action == "nets":
        base.matches = _matches_nets(modules, module, name, limit)
        base.summary = f"Found {len(base.matches)} net(s)."
        return base
    if action == "fanout":
        if not name:
            raise InvalidInput("fanout requires name (a net-name substring).")
        base.matches = _fanout(modules, name, limit)
        base.summary = f"Found {len(base.matches)} net group(s) and their loads."
        return base
    if action == "diff":
        if not other:
            raise InvalidInput("diff requires other (a second netlist path).")
        other_path = app.paths.resolve_read(other, must_exist=True)
        other_data = _load(other_path)
        other_modules = _modules(other_data)
        other_counts = _cell_counts(other_modules)
        added = {key: value for key, value in counts.items() if key not in other_counts}
        removed = {key: value for key, value in other_counts.items() if key not in counts}
        changed = {
            key: {"current": counts[key], "other": other_counts[key]}
            for key in counts.keys() & other_counts.keys()
            if counts[key] != other_counts[key]
        }
        base.stats = {
            "other": app.paths.display(other_path),
            "added_cell_types": added,
            "removed_cell_types": removed,
            "changed_cell_types": changed,
            "modules_added": sorted(set(modules) - set(other_modules)),
            "modules_removed": sorted(set(other_modules) - set(modules)),
        }
        base.summary = f"Compared {len(modules)} module(s) with {len(other_modules)} module(s)."
        return base
    base.modules = [_module_info(name, module) for name, module in modules.items() if isinstance(module, dict)][:limit]
    base.stats = {
        "cell_count": sum(counts.values()),
        "top_modules": [info.name for info in base.modules[:10]],
    }
    return base
